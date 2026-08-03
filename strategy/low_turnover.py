"""
低换手率因子策略

核心逻辑：选换手率最低的冷门股——赚散户不关注造成的定价低效。
学术依据：低流动性溢价（Illiquidity Premium），冷门股被市场忽视→低效定价→回归合理价值。
"""
import pandas as pd
from loguru import logger

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class LowTurnoverStrategy(PortfolioStrategy):
    """
    低换手率因子（冷门股策略）

    散户注意力偏差利用：
    - 散户只关注热门股（换手率高、成交活跃的）
    - 冷门股无人研究、无人关注 → 价格可能被低估
    - 系统化地买入冷门股，等待市场发现价值
    """

    def __init__(
        self,
        top_n: int = 10,
        rebalance_freq: str = "monthly",
        min_price: float = 5.0,
        max_pe: float = 100.0,
    ):
        """
        Args:
            top_n: 选换手率最低的前 N 只
            rebalance_freq: 调仓频率
            min_price: 最低股价
            max_pe: 最高 PE（排除亏损或高估值）
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
        return "低换手率因子"

    @property
    def description(self) -> str:
        return (f"选20日平均换手率最低的前{self.top_n}只，等权持有，月度轮动。"
                f"利用散户注意力偏差，在冷门股中寻找被低估的机会。")

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        """
        低换手率选股

        Steps:
        1. 过滤：价格 > min_price, PE 合理
        2. 排除换手率为0的（可能是停牌）
        3. 按换手率升序排列
        4. 选前 N 只
        5. 等权分配
        """
        df = universe_df.copy()

        # 基础过滤
        if 'close' in df.columns:
            df = df[df['close'] >= self.min_price]

        if 'pe' in df.columns:
            df_pe = df[(df['pe'] > 0) & (df['pe'] < self.max_pe)]
            if len(df_pe) >= self.top_n:
                df = df_pe

        # 换手率排序
        if 'turnover' in df.columns:
            # 排除换手率为0或极低的（可能停牌或数据异常）
            df = df[df['turnover'] > 0.1]
            df = df.sort_values('turnover', ascending=True)
        else:
            logger.warning(f"[低换手率] {date.date()} 无换手率数据")
            return {}

        # 选前 N 只
        selected = df.head(self.top_n)

        if len(selected) == 0:
            logger.warning(f"[低换手率] {date.date()} 无符合条件的股票")
            return {}

        # 等权分配
        weight = 0.95 / len(selected)
        weights = {sym: weight for sym in selected.index}

        logger.debug(f"[低换手率] {date.date()} 选股 {len(weights)} 只，"
                     f"最低换手率 {selected['turnover'].iloc[0]:.2f}%")
        return weights
