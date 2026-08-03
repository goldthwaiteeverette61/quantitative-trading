"""
微盘股 Q4 伏击系统 (Retail Harvest Q4)

降维打击专用策略：
1. 三重排雷：股价>5元 (避开仙股和ST退市潮)，MA60上方 (避开单边暴跌的飞刀)，换手率过滤僵尸和游资。
2. 潜伏 Q4：按动量排序，只买处于 60%~80% 全市场方位的温和上涨标的（稳健埋伏）。
3. 天然止盈：一旦股票由于暴涨进入全市场前20% (变成了 Q5 妖股)，将天然被此策略剥离并卖给追涨的散户。
"""

import pandas as pd
import numpy as np
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig

class RetailHarvestQ4Strategy(PortfolioStrategy):
    def __init__(
        self,
        top_n: int = 20,
        rebalance_freq: str = "monthly",
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=top_n,
        ))
        self.top_n = top_n

    @property
    def name(self) -> str:
        return "微盘Q4伏击"

    @property
    def description(self) -> str:
        return "量价极简防雷：股价>5且站稳季线。专挑温和爬升的小盘股进行埋伏，等其加速成为全场焦点时获利派发给狂热散户。极其侧重绝对收益下的撤离与风险对冲体系。"

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        df = universe_df.copy()

        # ---------- 1. 绝对防守：死亡排雷网 ---------- #

        # 排雷A：剔除低价股。跌破5元的往往基本面出恶性问题，容易触动退市或被大量低端游资收割。
        if 'close' in df.columns:
            df = df[df['close'] >= 5.0]

        # 排雷B：长线趋势必须向好（站在 MA60 季线上）。绝不在下降通道“捡漏”。
        if 'ma60_deviation' in df.columns:
            df = df[df['ma60_deviation'] > 0]

        # 排雷C：换手率剔除极端。太低(僵尸没有流动性)，太高(顶部派发期极度危险，不给接盘侠买单)。
        # 取中间的稳健换手。
        if 'turnover' in df.columns:
            valid_turnovers = df[df['turnover'] > 0]
            if not valid_turnovers.empty:
                q_low = valid_turnovers['turnover'].quantile(0.1)
                q_high = valid_turnovers['turnover'].quantile(0.8)
                df = df[(df['turnover'] >= q_low) & (df['turnover'] <= q_high)]

        if len(df) < 5:
            return {}

        # ---------- 2. 进攻端：Q4 温和伏击 ---------- #

        mom_col = 'momentum_20d' if 'momentum_20d' in df.columns else 'change_pct'
        if mom_col not in df.columns:
            return {}

        # 升序排列：从小到大
        df = df.sort_values(mom_col, ascending=True)
        total = len(df)

        # Q4 阶段：干掉最差的 60% (垃圾和震荡)，也干掉最惹眼的 20% (已经是游资和散户在狂欢的高危妖股区)
        start_idx = int(total * 0.60)
        end_idx = int(total * 0.80)

        q4_pool = df.iloc[start_idx:end_idx]

        if len(q4_pool) < 3:
            return {}

        # ---------- 3. 终选：找出 Q4 里面最不受关注的“闷声爬升股” ---------- #

        # 同样的涨幅里，挑选波动率最低的，证明主力高度控盘或者缓慢收集筹码（散户最没耐心看的温和阳线）
        vol_col = 'volatility_60d' if 'volatility_60d' in q4_pool.columns else 'volatility'
        if vol_col in q4_pool.columns:
            q4_pool = q4_pool.sort_values(vol_col, ascending=True)

        selected = q4_pool.head(self.top_n)

        weight = 1.0 / len(selected)
        weights = {sym: weight for sym in selected.index}

        return weights
