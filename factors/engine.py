"""
因子计算统一引擎 (Factor Engine)

一站式入口，整合三大模块:
  - friction: 摩擦与风险因子 (Amihud, 换手率波动, 特质波动率)
  - quality:  质量因子 (F-Score, 应计项目, 研发投入)
  - valuation: 估值因子 (PEG)

使用方式:
    from factors.engine import FactorEngine
    engine = FactorEngine()
    result = engine.compute_all(symbols=['000001', '600519'], days=200)
"""
import pandas as pd
import numpy as np
from typing import Optional
from loguru import logger
from concurrent.futures import ThreadPoolExecutor, as_completed

from factors.friction import add_friction_factors, build_market_returns
from factors.quality import compute_quality_factors
from factors.valuation import batch_compute_peg


class FactorEngine:
    """
    多维因子计算引擎

    整合日线衍生因子（高频）和财报衍生因子（低频）的统一入口。
    """

    def __init__(self):
        from data.database import DatabaseManager
        self.db = DatabaseManager()

    def compute_friction_factors(
        self,
        stock_dfs: dict[str, pd.DataFrame],
        window: int = 20,
        idio_window: int = 60,
    ) -> dict[str, pd.DataFrame]:
        """
        批量计算摩擦与风险类因子

        Args:
            stock_dfs: {symbol: DataFrame} 已加载的日线数据
            window: Amihud/turnover_vol 滚动窗口
            idio_window: CAPM 回归窗口

        Returns:
            {symbol: DataFrame with factor columns added}
        """
        # 构建市场等权收益率
        market_ret = build_market_returns(stock_dfs)
        logger.info(f"[因子引擎] 市场代理变量已构建: {len(market_ret)} 个交易日")

        results = {}
        for symbol, df in stock_dfs.items():
            if df is None or df.empty:
                continue
            results[symbol] = add_friction_factors(
                df, window=window,
                market_returns=market_ret,
                idio_window=idio_window,
            )

        logger.info(f"[因子引擎] 摩擦因子计算完成: {len(results)} 只")
        return results

    def compute_quality_factors(
        self,
        symbols: list[str],
        max_workers: int = 3,
    ) -> dict[str, dict]:
        """
        批量计算质量因子（F-Score / Accruals / R&D）

        注意: 需要从新浪拉取财报，有网络请求。
              max_workers 控制并发数，避免被封。

        Args:
            symbols: 股票代码列表
            max_workers: 最大并发请求数

        Returns:
            {symbol: quality_factor_dict}
        """
        results = {}

        def _fetch_one(sym):
            try:
                return sym, compute_quality_factors(sym)
            except Exception as e:
                logger.error(f"[质量因子] {sym} 计算失败: {e}")
                return sym, None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_one, s): s for s in symbols}
            for i, future in enumerate(as_completed(futures)):
                sym, result = future.result()
                if result is not None:
                    results[sym] = result
                if (i + 1) % 10 == 0:
                    logger.info(f"[质量因子] 进度: {i+1}/{len(symbols)}")

        logger.info(f"[因子引擎] 质量因子计算完成: {len(results)} 只")
        return results

    def compute_peg_factors(
        self,
        stock_dfs: dict[str, pd.DataFrame],
        forecast_date: str = None,
    ) -> dict[str, Optional[float]]:
        """
        批量计算 PEG 估值因子

        Args:
            stock_dfs: {symbol: DataFrame} 含 pe 列的日线数据
            forecast_date: 盈利预告期

        Returns:
            {symbol: PEG or None}
        """
        # 从日线数据中提取最新 PE-TTM
        pe_map = {}
        for symbol, df in stock_dfs.items():
            if df is not None and not df.empty and 'pe' in df.columns:
                pe_val = df['pe'].iloc[-1]
                if pe_val and pe_val > 0:
                    pe_map[symbol] = pe_val

        return batch_compute_peg(pe_map, forecast_date)

    def compute_all(
        self,
        symbols: list[str],
        days: int = 200,
        friction_window: int = 20,
        idio_window: int = 60,
        quality_workers: int = 3,
        skip_quality: bool = False,
        skip_peg: bool = False,
    ) -> dict:
        """
        一站式计算全部因子

        Args:
            symbols: 股票代码列表
            days: 日线数据天数
            friction_window: 摩擦因子滚动窗口
            idio_window: CAPM 回归窗口
            quality_workers: 质量因子并发数
            skip_quality: 跳过质量因子（避免网络请求）
            skip_peg: 跳过 PEG 计算

        Returns:
            {
                'friction': {symbol: DataFrame},   # 日线+摩擦因子
                'quality': {symbol: dict},          # 质量因子
                'peg': {symbol: float or None},     # PEG 估值
                'summary': DataFrame,               # 横截面因子汇总表
            }
        """
        logger.info(f"[因子引擎] 启动全量计算: {len(symbols)} 只, {days} 天")

        # 1. 加载日线数据
        stock_dfs = {}
        for s in symbols:
            df = self.db.load_klines(s, days)
            if df is not None and not df.empty:
                stock_dfs[s] = df

        logger.info(f"[因子引擎] 日线数据加载: {len(stock_dfs)}/{len(symbols)} 只")

        # 2. 摩擦因子
        friction_results = self.compute_friction_factors(
            stock_dfs, window=friction_window, idio_window=idio_window
        )

        # 3. 质量因子
        quality_results = {}
        if not skip_quality:
            quality_results = self.compute_quality_factors(
                list(stock_dfs.keys()), max_workers=quality_workers
            )

        # 4. PEG
        peg_results = {}
        if not skip_peg:
            peg_results = self.compute_peg_factors(stock_dfs)

        # 5. 构建横截面汇总表
        summary_rows = []
        for sym in stock_dfs:
            row = {'symbol': sym}

            # 摩擦因子（取最后一行）
            if sym in friction_results:
                f_df = friction_results[sym]
                for col in ['amihud', 'amihud_log', 'turnover_vol', 'total_vol', 'idio_vol']:
                    if col in f_df.columns:
                        row[col] = f_df[col].iloc[-1]

            # 股息率
            if sym in stock_dfs and 'dividend_yield' in stock_dfs[sym].columns:
                row['dividend_yield'] = stock_dfs[sym]['dividend_yield'].iloc[-1]

            # 质量因子
            if sym in quality_results:
                q = quality_results[sym]
                row['fscore'] = q.get('fscore')
                row['accruals'] = q.get('accruals')
                row['rd_intensity'] = q.get('rd_intensity')
                row['roa'] = q.get('roa')
                row['gross_margin'] = q.get('gross_margin')
                row['leverage'] = q.get('leverage')

            # PEG
            if sym in peg_results:
                row['peg'] = peg_results[sym]

            summary_rows.append(row)

        summary = pd.DataFrame(summary_rows)
        if not summary.empty:
            summary = summary.set_index('symbol')

        return {
            'friction': friction_results,
            'quality': quality_results,
            'peg': peg_results,
            'summary': summary,
        }
