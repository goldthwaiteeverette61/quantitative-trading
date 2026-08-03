"""
反转因子策略

核心逻辑：选近 5 日跌幅最大的股票——赚散户恐慌抛售后的均值回归。
学术依据：短期反转效应（Short-term Reversal），散户过度反应修正。
"""
import pandas as pd
from loguru import logger

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class ReversalStrategy(PortfolioStrategy):
    """
    短期反转因子

    散户心理弱点利用：
    - 散户恐慌杀跌 → 短期跌过头 → 买入后等待价格修复
    - 散户追涨热门股 → 短期涨过头 → 我们避开泡沫
    - 月度轮动，只做「超跌反弹」的票
    """

    def __init__(
        self,
        top_n: int = 10,
        rebalance_freq: str = "monthly",
        min_price: float = 5.0,
        max_volatility: float = 80.0,
    ):
        """
        Args:
            top_n: 选跌幅最大的前 N 只
            rebalance_freq: 调仓频率
            min_price: 最低股价（避开极低价垃圾股）
            max_volatility: 最大波动率阈值（波动太大的=有基本面问题，排除）
        """
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=top_n,
        ))
        self.top_n = top_n
        self.min_price = min_price
        self.max_volatility = max_volatility

    @property
    def name(self) -> str:
        return "短期反转因子"

    @property
    def description(self) -> str:
        return (f"选近5日跌幅最大的前{self.top_n}只，等权持有，月度轮动。"
                f"利用散户恐慌抛售导致的超跌，赚均值回归的钱。")

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        """
        反转选股

        Steps:
        1. 过滤：价格 > min_price, 波动率 < max_volatility
        2. 按近5日涨跌幅升序排列（跌得最多的排前面）
        3. 选前 N 只（跌最多的）
        4. 等权分配
        """
        df = universe_df.copy()

        # 基础过滤
        if 'close' in df.columns:
            df = df[df['close'] >= self.min_price]

        # 波动率过滤：排除波动过大的（可能是基本面出问题）
        if 'volatility' in df.columns:
            vol = df['volatility']
            df = df[(vol > 0) & (vol < self.max_volatility)]

        # 按5日动量升序（跌得最多的排前面）
        if 'momentum_5d' in df.columns:
            # 只选下跌的票（momentum_5d < 0）
            df = df[df['momentum_5d'] < 0]
            df = df.sort_values('momentum_5d', ascending=True)
        elif 'change_pct' in df.columns:
            df = df[df['change_pct'] < 0]
            df = df.sort_values('change_pct', ascending=True)
        else:
            logger.warning(f"[反转] {date.date()} 无动量数据")
            return {}

        # 选前 N 只
        selected = df.head(self.top_n)

        if len(selected) == 0:
            logger.warning(f"[反转] {date.date()} 无符合条件的股票")
            return {}

        # 等权分配
        weight = 0.95 / len(selected)
        weights = {sym: weight for sym in selected.index}

        logger.debug(f"[反转] {date.date()} 选股 {len(weights)} 只，"
                     f"最大跌幅 {selected['momentum_5d'].iloc[0]:.1f}%"
                     if 'momentum_5d' in selected.columns else
                     f"[反转] {date.date()} 选股 {len(weights)} 只")
        return weights
