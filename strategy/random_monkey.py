"""
🐒 随机猴子策略 (Random Monkey)

专门用于检验策略有效性（蒙特卡洛随机排列检验）。
该策略像一只蒙着眼睛的猴子：
在每次调仓点，面对和你的真实策略完全相同的股票池（自动剔除退市、ST等），
它**完全随机**地抓取规定数目（top_n）的股票，平均买入并持有到下一期。

如果跑 100 次猴子策略，算出一条收益钟形曲线，
而你辛苦开发的策略的收益不仅没有跑到曲线右侧尾部，反而落在平均线里，
那就证明你所谓战胜市场的“策略Alpha”，只是一种数据挖掘过拟合出来的错觉（纯属运气）。
"""

import random
import pandas as pd
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig

class RandomMonkeyStrategy(PortfolioStrategy):
    def __init__(
        self,
        top_n: int = 20,
        rebalance_freq: str = "monthly",
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=top_n,
        ))

    @property
    def name(self) -> str:
        return "🐵 无知猴子随机基准"

    @property
    def description(self) -> str:
        return f"就像丢飞镖一样，在池子里盲狙 {self.rebalance_config.max_holdings} 只非ST股等权买入，用于蒙特卡洛统计学检定（P-Value）。"

    @property
    def author(self) -> str:
        return "概率论之神"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        df = universe_df.copy()

        # 基础排雷，只要猴子不买确定会退市的拉胯标的就行
        # 1. 过滤历史真实的 ST 标识
        if 'isST' in df.columns:
            df = df[df['isST'] == 0]

        # 2. 过滤停牌和奇奇怪怪的数据 (至少要有交易价)
        if 'close' in df.columns:
            df = df[df['close'] > 0]

        available_list = list(df.index)

        # 可选数目不够时，有多少买多少
        n = min(self.rebalance_config.max_holdings, len(available_list))
        if n == 0:
            return {}

        # 核心：完全随机摇号
        selected = random.sample(available_list, n)

        # 等权配置，留出 5% 作为现金抗滑点摩擦
        weight = 0.95 / n
        weights = {sym: weight for sym in selected}

        return weights
