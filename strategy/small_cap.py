"""
小市值因子策略

核心逻辑：按流通市值从小到大排序，选前 N 只等权持有，月度轮动。
核心哲学体现：降维打击中小盘，利用机构无法进出的流动性限制获取超额收益。
"""
import pandas as pd
from loguru import logger

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class SmallCapStrategy(PortfolioStrategy):
    """
    小市值因子策略

    「降维打击」的数字化表达：
    - 避开大盘股绞肉机
    - 在中小盘市场中，利用分散持仓 + 系统化轮动获取因子溢价
    - 等权持有，最大化分散个股风险
    """

    def __init__(
        self,
        top_n: int = 10,
        rebalance_freq: str = "monthly",
        min_price: float = 3.0,
        max_pe: float = 200.0,
    ):
        """
        Args:
            top_n: 选取最小市值的前 N 只
            rebalance_freq: 调仓频率
            min_price: 最低股价过滤（排除低价垃圾股）
            max_pe: 最高 PE 过滤（排除亏损股）
        """
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=top_n,
        ))
        self.top_n = top_n
        self.min_price = min_price
        self.max_pe = max_pe

    @property
    def name(self) -> str:
        return "小市值因子"

    @property
    def description(self) -> str:
        return (f"按市值从小到大排序，选前{self.top_n}只等权持有，"
                f"月度轮动。降维打击中小盘，利用机构资金容量限制获取超额收益。")

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        """
        小市值选股

        Steps:
        1. 过滤：股价 > min_price, PE 在合理范围
        2. 按市值升序排列
        3. 选前 top_n 只
        4. 等权分配
        """
        df = universe_df.copy()

        # 基础过滤
        if 'close' in df.columns:
            df = df[df['close'] >= self.min_price]

        if 'pe' in df.columns:
            df = df[(df['pe'] > 0) & (df['pe'] < self.max_pe)]

        if 'market_cap' in df.columns:
            df = df[df['market_cap'] > 0]
            # 按市值升序排序
            df = df.sort_values('market_cap', ascending=True)
        else:
            # 如果没有市值数据，按收盘价近似替代（价格低常意味着小盘）
            df = df.sort_values('close', ascending=True)

        # 选前 N 只
        selected = df.head(self.top_n)

        if len(selected) == 0:
            logger.warning(f"[小市值] {date.date()} 无符合条件的股票")
            return {}

        # 等权分配（留 5% 的现金缓冲）
        weight = 0.95 / len(selected)
        weights = {sym: weight for sym in selected.index}

        logger.debug(f"[小市值] {date.date()} 选股 {len(weights)} 只，"
                     f"最小市值 {selected['market_cap'].iloc[0]:.1f}亿")
        return weights
