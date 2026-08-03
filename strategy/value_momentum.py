"""
低估值 + 高动量复合因子策略

核心逻辑：在估值合理的前提下，选择动量最强的股票。
双因子叠加降低噪声，捕捉「便宜又在涨」的机会。
"""
import pandas as pd
import numpy as np
from loguru import logger

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class ValueMomentumStrategy(PortfolioStrategy):
    """
    低估值 + 高动量复合因子

    策略逻辑：
    1. 估值过滤：PE < 全市场中位数（排除泡沫股）
    2. 动量排序：按 20 日涨幅降序
    3. 选前 N 只等权持有
    4. 月度轮动

    因子叠加的意义：
    - 单独用估值：可能买到「价值陷阱」（便宜但一直跌）
    - 单独用动量：可能追高到泡沫股
    - 叠加使用：「便宜且正在涨」= 更高的胜率
    """

    def __init__(
        self,
        top_n: int = 10,
        rebalance_freq: str = "monthly",
        pe_quantile: float = 0.5,
        min_price: float = 3.0,
    ):
        """
        Args:
            top_n: 持仓数量
            rebalance_freq: 调仓频率
            pe_quantile: PE 分位数阈值（低于此分位数的才入选）
            min_price: 最低股价
        """
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=top_n,
        ))
        self.top_n = top_n
        self.pe_quantile = pe_quantile
        self.min_price = min_price

    @property
    def name(self) -> str:
        return "价值动量复合因子"

    @property
    def description(self) -> str:
        return (f"PE低于{int(self.pe_quantile*100)}%分位 + 20日动量最强的前{self.top_n}只，"
                f"月度轮动。双因子叠加，捕捉「便宜又在涨」的机会。")

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        """
        价值+动量双因子选股

        Steps:
        1. 基础筛选：价格 > min_price, PE > 0
        2. 估值筛选：PE < 中位数
        3. 动量排序：按涨跌幅降序（或按收盘价/均线比率）
        4. 选前 N 只等权
        """
        df = universe_df.copy()

        # 基础过滤
        if 'close' in df.columns:
            df = df[df['close'] >= self.min_price]

        # PE 筛选
        if 'pe' in df.columns and df['pe'].sum() > 0:
            df_valid_pe = df[df['pe'] > 0]
            if len(df_valid_pe) > 0:
                pe_threshold = df_valid_pe['pe'].quantile(self.pe_quantile)
                df = df_valid_pe[df_valid_pe['pe'] <= pe_threshold]
        else:
            # 无 PE 数据时跳过估值筛选
            pass

        if len(df) == 0:
            logger.warning(f"[价值动量] {date.date()} 估值筛选后无股票")
            return {}

        # 动量排序
        if 'change_pct' in df.columns:
            df = df.sort_values('change_pct', ascending=False)
        elif 'momentum_20d' in df.columns:
            df = df.sort_values('momentum_20d', ascending=False)
        else:
            # 没有动量数据时按价格反向排序（便宜股优先）
            df = df.sort_values('close', ascending=True)

        # 选前 N 只
        selected = df.head(self.top_n)

        if len(selected) == 0:
            return {}

        # 等权分配（留 5% 现金缓冲）
        weight = 0.95 / len(selected)
        weights = {sym: weight for sym in selected.index}

        logger.debug(f"[价值动量] {date.date()} 选股 {len(weights)} 只，"
                     f"PE阈值 {pe_threshold:.1f}" if 'pe_threshold' in dir() else
                     f"[价值动量] {date.date()} 选股 {len(weights)} 只")
        return weights
