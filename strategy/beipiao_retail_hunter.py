"""
围猎散户专属策略

核心理念：
散户有三大系统性弱点，可以被量化并反向利用：

1. 处置效应 (Disposition Effect)
   - 散户倾向于"赢了就跑、亏了死扛"
   - 信号：上涨时缩量（散户急着兑现），下跌时放量（散户恐慌割肉）
   - 反向操作：买入"上涨缩量"票（散户已把筹码交出来，上方无抛压）

2. 注意力偏差 (Attention Bias)
   - 散户只关注最近暴涨/暴跌的票，忽视默默走强但不起眼的票
   - 信号：短中期动量劈叉大（短期暴涨但中期平淡 = 散户追高）
   - 反向操作：买入"中期强但短期平静"的票（机构悄悄建仓）

3. 恐慌性割肉 (Panic Selling)
   - 散户在下跌中放量卖出（恐慌割肉后形成"黄金坑"）
   - 信号：近5日跌幅>3% 且 成交量放大到20日均量的1.5倍以上
   - 反向操作：在散户恐慌出局后接住筹码

本策略与北漂V4互补：
- V4 侧重"微盘+基本面+排雷"
- 本策略侧重"散户行为信号+超跌反弹"，不局限于微盘
"""
import pandas as pd
from loguru import logger

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class BeipiaoRetailHunter(PortfolioStrategy):
    """
    围猎散户策略

    选股逻辑：
    1. 基础过滤：非ST、股价>1元、上市>150天、流通市值<50亿
    2. 基本面：ROE(PB/PE) > 8%（门槛比V4低，因为主要靠行为因子）
    3. 行为因子综合打分：
       - 处置效应分（disposition_ratio）：高分 = 散户抛压释放充分
       - 超跌放量分（turnover_surge_in_decline）：高分 = 散户恐慌后的黄金坑
       - 动量劈叉分（momentum_diverge）：低分 = 市场平静，非追高
       - 炸板标记：有则直接排除
    """
    def __init__(
        self,
        top_n: int = 10,
        min_roe: float = 0.08,            # ROE 门槛更低（主要靠行为因子）
        max_market_cap: float = 50.0,      # 市值上限放宽到50亿
        rebalance_freq: str = "weekly",
    ):
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=top_n))
        self.top_n = top_n
        self.min_roe = min_roe
        self.max_market_cap = max_market_cap

    @property
    def name(self) -> str:
        return "围猎散户_行为因子版"

    @property
    def description(self) -> str:
        return (
            f"利用散户处置效应+恐慌割肉信号反向操作，"
            f"ROE>{self.min_roe*100}% + 市值<{self.max_market_cap}亿"
        )

    def get_target_weights(self, date: pd.Timestamp, universe_df: pd.DataFrame) -> dict[str, float]:
        df = universe_df.copy()

        # ===== 1. 基础过滤 =====
        df = df[df['isST'] == 0]
        if 'close' in df.columns:
            df = df[df['close'] > 1.0]
        if 'list_days' in df.columns:
            df = df[df['list_days'] > 150]

        # ===== 2. 市值过滤 =====
        if 'market_cap' in df.columns:
            df = df[df['market_cap'] <= self.max_market_cap]

        # ===== 3. 基本面过滤（较宽松） =====
        if 'pe' in df.columns and 'pb' in df.columns:
            df = df[(df['pe'] > 0) & (df['pb'] > 0)]
            df['roe_est'] = df['pb'] / df['pe']
            df = df[df['roe_est'] >= self.min_roe]

        # ===== 4. 排除危险信号 =====
        # 炸板票不碰
        if 'failed_limit_up' in df.columns:
            df = df[df['failed_limit_up'] == 0]

        # 近5日暴涨的不碰（>15%，散户追高区间）
        if 'momentum_5d' in df.columns:
            df = df[df['momentum_5d'] <= 15.0]

        if len(df) == 0:
            return {}

        # ===== 5. 散户行为因子综合打分 =====

        # 因子A：处置效应（越高越好 → 散户筹码释放完毕）
        if 'disposition_ratio' in df.columns:
            df['score_disposition'] = df['disposition_ratio'].rank(pct=True)  # 0~1
        else:
            df['score_disposition'] = 0.5

        # 因子B：超跌放量（>1 表示下跌中放量，越高 = 散户恐慌越剧烈 = 黄金坑）
        if 'turnover_surge_in_decline' in df.columns:
            # 只对有放量信号的票打分（无信号的 = 0，不加分）
            df['score_panic'] = df['turnover_surge_in_decline'].rank(pct=True)
        else:
            df['score_panic'] = 0.5

        # 因子C：动量劈叉（越小越好 → 市场平静，不是追高追跌）
        if 'momentum_diverge' in df.columns:
            df['score_calm'] = df['momentum_diverge'].rank(pct=True, ascending=False)  # 低劈叉排名靠前
        else:
            df['score_calm'] = 0.5

        # 因子D：市值因子（越小越好，微盘加分）
        if 'market_cap' in df.columns:
            df['score_cap'] = df['market_cap'].rank(pct=True, ascending=False)
        else:
            df['score_cap'] = 0.5

        # 综合分 = 处置效应 35% + 超跌放量 30% + 市场平静度 15% + 市值 20%
        df['total_score'] = (
            df['score_disposition'] * 0.35 +
            df['score_panic'] * 0.30 +
            df['score_calm'] * 0.15 +
            df['score_cap'] * 0.20
        )

        # 按总分降序排列
        df = df.sort_values('total_score', ascending=False)
        selected = df.head(self.top_n)

        if len(selected) == 0:
            return {}

        weight = 0.95 / len(selected)
        return {sym: weight for sym in selected.index}
