"""
低波动 + 中期动量策略（V2）

学术依据：
- 低波动率异象 (Low Volatility Anomaly)：Ang et al. (2006)
- 中期动量 (Momentum)：Jegadeesh & Titman (1993)
- 均线趋势过滤：只在趋势市场中做多，规避系统性下跌

纯量价因子，不依赖 PE/PB。

V2 改进点（对比 V1 的 -94%）：
1. 波动率窗口从 20日 → 60日，样本量更充分
2. 动量窗口从 20日 → 60日，捕捉中期趋势而非短期噪声
3. 增加均线趋势过滤：股价必须在 60日均线之上（上升趋势）
4. 增加仓位控制：全市场走弱时（多数股票低于均线）降低仓位
"""
import pandas as pd
import numpy as np
from loguru import logger

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class LowVolMomentumStrategy(PortfolioStrategy):
    """
    低波动 + 中期动量（V2）

    核心逻辑：
    1. 趋势过滤：只选股价在 60 日均线之上的股票（上升趋势）
    2. 波动率过滤：用 60 日波动率，剔除最高的 50%
    3. 动量排序：按 60 日涨幅降序
    4. 仓位控制：如果低于一半股票处于上升趋势，降低仓位到 50%
    5. 选前 N 只等权持有
    """

    def __init__(
        self,
        top_n: int = 20,
        rebalance_freq: str = "monthly",
        vol_percentile: float = 0.5,
        min_price: float = 3.0,
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=top_n,
        ))
        self.top_n = top_n
        self.vol_percentile = vol_percentile
        self.min_price = min_price

    @property
    def name(self) -> str:
        return "低波动量"

    @property
    def description(self) -> str:
        return (f"低波动+动量双因子V2：60日波动率筛选最稳定的{int(self.vol_percentile*100)}%股票，"
                f"叠加60日动量排序选前{self.top_n}只。"
                f"均线趋势过滤＋仓位控制，纯量价因子。")

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        """
        低波动 + 中期动量 双因子选股（V2）

        Steps:
        1. 价格过滤：> min_price
        2. 趋势过滤：股价在 60 日均线之上
        3. 波动率过滤：60 日波动率最低的一半
        4. 动量排序：60 日涨幅降序
        5. 仓位控制：根据市场整体趋势动态调整
        """
        df = universe_df.copy()

        # ---- 1. 基础价格过滤 ----
        if 'close' in df.columns:
            df = df[df['close'] >= self.min_price]

        if len(df) < 10:
            return {}

        total_stocks = len(df)

        # ---- 2. 趋势过滤：股价 > 60日均线 ----
        if 'ma60_deviation' in df.columns:
            df_uptrend = df[df['ma60_deviation'] > 0]
            uptrend_ratio = len(df_uptrend) / total_stocks if total_stocks > 0 else 0

            if len(df_uptrend) >= 10:
                df = df_uptrend
            else:
                # 市场太弱，放宽到 MA20
                if 'ma20_deviation' in df.columns:
                    df_ma20 = df[df['ma20_deviation'] > 0]
                    if len(df_ma20) >= 10:
                        df = df_ma20
                        uptrend_ratio = len(df_ma20) / total_stocks
                    else:
                        # 市场极弱，不开仓
                        logger.debug(f"[低波动量] {date.date()} 市场极弱，放弃开仓 "
                                     f"(仅 {len(df_uptrend)} 只在MA60上方)")
                        return {}
                else:
                    return {}
        else:
            uptrend_ratio = 1.0

        if len(df) < 5:
            return {}

        # ---- 3. 波动率过滤（优先用 60日，退而用 20日）----
        vol_col = 'volatility_60d' if 'volatility_60d' in df.columns else 'volatility'
        if vol_col in df.columns:
            df = df[df[vol_col] > 0]
            if len(df) < 5:
                return {}

            vol_threshold = df[vol_col].quantile(self.vol_percentile)
            df_low_vol = df[df[vol_col] <= vol_threshold]

            if len(df_low_vol) < 5:
                vol_threshold = df[vol_col].quantile(0.7)
                df_low_vol = df[df[vol_col] <= vol_threshold]

            df = df_low_vol

        if len(df) < 3:
            return {}

        # ---- 4. 动量排序（优先用 60日，退而用 20日）----
        mom_col = 'momentum_60d' if 'momentum_60d' in df.columns else 'momentum_20d'
        if mom_col in df.columns:
            # 只要正动量
            df_positive = df[df[mom_col] > 0]
            if len(df_positive) >= 3:
                df = df_positive
            df = df.sort_values(mom_col, ascending=False)
        elif 'change_pct' in df.columns:
            df = df.sort_values('change_pct', ascending=False)

        # ---- 5. 选前 N 只等权 ----
        selected = df.head(self.top_n)

        if len(selected) == 0:
            return {}

        # 仓位控制：市场强时 90% 仓位，市场弱时 50%
        if uptrend_ratio >= 0.5:
            total_weight = 0.90
        elif uptrend_ratio >= 0.3:
            total_weight = 0.60
        else:
            total_weight = 0.40

        weight = total_weight / len(selected)
        weights = {sym: weight for sym in selected.index}

        if mom_col in selected.columns:
            logger.debug(
                f"[低波动量] {date.date()} 选股 {len(weights)} 只 | "
                f"趋势比 {uptrend_ratio:.0%} → 仓位 {total_weight:.0%} | "
                f"波动率阈值 {vol_threshold:.1f}% | "
                f"动量 {selected[mom_col].min():.1f}%~{selected[mom_col].max():.1f}%"
            )
        return weights
