"""
估值因子计算模组 (Valuation Factors)

PEG = PE / 预期EPS增长率
数据来源:
  - PE: 本地 daily_kline 表（已有 pe_ttm 字段）
  - 预期EPS增长率: AKShare stock_yjyg_em（东方财富盈利预告）
"""
import pandas as pd
import numpy as np
from typing import Optional
from loguru import logger


def fetch_earnings_forecast(date: str = None) -> pd.DataFrame:
    """
    获取全市场盈利预测数据

    Args:
        date: 报告期，如 '20241231'，默认取最近年报期

    Returns:
        DataFrame with columns: ['股票代码', '预测数值', '业绩变动幅度', '预告类型']
    """
    import akshare as ak

    if date is None:
        # 自动推断最近年报期
        from datetime import datetime
        now = datetime.now()
        year = now.year - 1 if now.month < 5 else now.year
        date = f"{year}1231"

    try:
        df = ak.stock_yjyg_em(date=date)
        if df is not None and not df.empty:
            logger.info(f"[盈利预告] 获取 {date} 共 {len(df)} 条")
            return df
    except Exception as e:
        logger.error(f"[盈利预告] 获取失败 {date}: {e}")

    return pd.DataFrame()


def compute_peg(pe: float, earnings_growth: float) -> Optional[float]:
    """
    计算 PEG 比率

    Args:
        pe: 市盈率 (PE-TTM)
        earnings_growth: 预期盈利增长率（百分比形式，如 20 代表 20%）

    Returns:
        PEG 值。规则:
          - PE <= 0: 亏损股，返回 None
          - 增长率 <= 0: 负增长，返回 None（PEG 对负增长无意义）
          - PEG < 1: 被低估
          - PEG = 1: 合理估值
          - PEG > 1: 被高估
    """
    if pe is None or pe <= 0:
        return None
    if earnings_growth is None or earnings_growth <= 0:
        return None

    return pe / earnings_growth


def batch_compute_peg(
    stock_pe_map: dict[str, float],
    forecast_date: str = None,
) -> dict[str, Optional[float]]:
    """
    批量计算 PEG

    Args:
        stock_pe_map: {股票代码: PE-TTM}
        forecast_date: 盈利预告期

    Returns:
        {股票代码: PEG 或 None}
    """
    forecast_df = fetch_earnings_forecast(forecast_date)

    result = {}
    if forecast_df.empty:
        logger.warning("[PEG] 无盈利预告数据，全部返回 None")
        return {code: None for code in stock_pe_map}

    # 建立 code -> 业绩变动幅度 的映射
    growth_map = {}
    for _, row in forecast_df.iterrows():
        code = str(row.get('股票代码', '')).zfill(6)
        growth = row.get('业绩变动幅度')
        if growth is not None and not (isinstance(growth, float) and np.isnan(growth)):
            try:
                growth_map[code] = float(growth)
            except (ValueError, TypeError):
                pass

    for code, pe in stock_pe_map.items():
        clean_code = code.split('.')[0].zfill(6)
        growth = growth_map.get(clean_code)
        result[code] = compute_peg(pe, growth)

    matched = sum(1 for v in result.values() if v is not None)
    logger.info(f"[PEG] 计算完成: {len(result)} 只 | 有效 {matched} 只 | 无效 {len(result)-matched} 只")

    return result
