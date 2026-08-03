"""
哑铃型配置策略 (Barbell 2026)

实施《2026年A股中小盘策略》顶层逻辑：
1. 退市预警底线：利用市盈率(PE)、市净率(PB)构建第一道防线，坚决剔除亏损和资不抵债标的。
2. 哑铃左端（50%）：质量压舱石。选取当前池子中估值最低、PB最安全的高股息特征类股票，防守流动性枯竭。
3. 哑铃右端（50%）：成长进攻矛。选取当前池子中的小盘股，依靠 Q4 温和动量（换手率健康）伏击散户FOMO爆发。
"""

import pandas as pd
import numpy as np
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig

class Barbell2026Strategy(PortfolioStrategy):
    def __init__(
        self,
        top_n: int = 20,
        rebalance_freq: str = "monthly",
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=top_n,
        ))

        # 哑铃两端各分一半席位
        self.left_n = max(1, top_n // 2)
        self.right_n = top_n - self.left_n

    @property
    def name(self) -> str:
        return "哑铃策略(红利+微盘)"

    @property
    def description(self) -> str:
        return "2026新规防线版：50%仓位买入极低PE/PB的红利平替白马（防守），50%仓位买入微盘Q4温和动量等待妖股化派发。兼顾不死和暴利。"

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        df = universe_df.copy()

        # ---------- 第 1 步：新规退市红线排雷 ---------- #
        # 1. 亏损股必死 (PE <= 0 代表负利润)
        if 'pe' in df.columns:
            df = df[df['pe'] > 0]

        # 2. 资不抵债必死 (PB <= 0 代表净资产为负)
        if 'pb' in df.columns:
            df = df[df['pb'] > 0]

        # 3. 历史真实的 ST 退市标识，一票否决
        if 'isST' in df.columns:
            df = df[df['isST'] == 0]

        # 4. 极低价股仙化风险 (过滤单边快速腰斩且破红线的低价股)
        if 'close' in df.columns:
            df = df[df['close'] >= 4.0]

        if len(df) < self.left_n + self.right_n:
            return {}

        # ---------- 第 2 步：构建哑铃左端（50%：质量红利压舱石） ---------- #
        # 逻辑：在存活的公司中，寻找 PE 在 0~15 之间，且 PB 极低、波动极小的股票伪装成中证红利
        left_pool = df[(df['pe'] < 20) & (df['pb'] < 2.0)].copy()

        # 如果符合红利区间的票不够，放宽 PE 限制
        if len(left_pool) < self.left_n:
            left_pool = df.copy()

        # 排序：优先买 PB 最低（最破净/最具安全边际）且波动率最低的收息防守股
        if 'volatility' in left_pool.columns:
            # 综合打分：PB排名 (升序) + 波动率排名 (升序)
            left_pool['score_left'] = left_pool['pb'].rank() + left_pool['volatility'].rank()
            left_pool = left_pool.sort_values('score_left', ascending=True)
        else:
            left_pool = left_pool.sort_values('pb', ascending=True)

        left_selected = left_pool.head(self.left_n)

        # 将左端已选股从右端进攻池中剔除，防止两手互搏
        df_right_pool = df.drop(index=left_selected.index)

        # ---------- 第 3 步：构建哑铃右端（50%：微盘 Q4 动量进攻） ---------- #
        # 逻辑：在剩余标的中，提取市值较小的（模拟中小盘/专精特新），计算 Q4 动量伏击阵地。
        if 'market_cap' in df_right_pool.columns:
            df_right_pool = df_right_pool.sort_values('market_cap', ascending=True)
            # 抛弃市值最大的那部分，只留市值偏小的作为弹性基础
            df_right_pool = df_right_pool.head(int(len(df_right_pool) * 0.8))

        mom_col = 'momentum_20d' if 'momentum_20d' in df_right_pool.columns else 'change_pct'
        if mom_col in df_right_pool.columns:
            df_right_pool = df_right_pool.sort_values(mom_col, ascending=True)

            # 提取全市场 60% ~ 80% 区间 (Q4 温和伏击组)
            total_right = len(df_right_pool)
            start_idx = int(total_right * 0.6)
            end_idx = int(total_right * 0.8)

            q4_pool = df_right_pool.iloc[start_idx:end_idx]
            if len(q4_pool) >= self.right_n:
                right_selected = q4_pool.head(self.right_n)
            else:
                right_selected = df_right_pool.tail(self.right_n) # 备用：如果没有 Q4，拿稍微强点的
        else:
            right_selected = df_right_pool.head(self.right_n)

        # ---------- 第 4 步：合成哑铃，资产调配 ---------- #
        weights = {}

        # 左端：防守阵地总资金占 50%
        if len(left_selected) > 0:
            w_left = 0.5 / len(left_selected)
            for sym in left_selected.index:
                weights[sym] = w_left

        # 右端：进攻阵地总资金占 50%
        if len(right_selected) > 0:
            w_right = 0.5 / len(right_selected)
            for sym in right_selected.index:
                weights[sym] = w_right

        if len(weights) > 0:
            logger.debug(f"[{self.name}] {date.date()} 调仓成功: 左端红利防守 {len(left_selected)}只，右端微盘进攻 {len(right_selected)}只")

        return weights
