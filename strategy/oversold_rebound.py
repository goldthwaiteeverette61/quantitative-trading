"""
A股特色的恐慌超跌均值回归策略 — Oversold Rebound

核心理念：放弃牛市追高，专做暴跌及恐慌末期的“带血筹码”。利用左侧技术指标（RSI深水区 + 布林带击穿）进行均值回归套利。
"""
import pandas as pd
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig

class OversoldReboundStrategy(PortfolioStrategy):
    """
    恐慌超跌反弹策略

    选股条件：
      1. MA20 乖离率 < -12%（深跌）
      2. 跌穿布林带下轨 (bb_pct_b < 0)
      3. RSI(14) 极度超卖 < 30
    风控（大盘宽度市场过滤器）：
      只在市场有一定恐慌时介入。如果全市场都在狂欢 (Breadth > 0.6) 可能会出现逆向失效，不碰。
      如果市场彻底冰点 (Breadth < 0.1) 恐慌盘可能没出尽，可以半仓或满仓视激进程度而定（本策略选0.1~0.4为甜区）。
    """

    def __init__(self, top_n: int = 10, min_score: float = 0.0, rebalance_freq: str = "weekly"):
        # 由于是短线抄底，周度调仓必须配合严格的日级止损。
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=top_n))
        self.top_n = top_n

    @property
    def name(self) -> str:
        return "恐慌超卖反弹"

    @property
    def category(self) -> str:
        return "反转"

    @property
    def description(self) -> str:
        return "极速左侧抄底：当大盘宽度偏低时，扫货处于RSI<30且跌破布林带下轨的重度超卖个股，赚取周级暴跌后的均值回归修复。"

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(self, date: pd.Timestamp, universe_df: pd.DataFrame) -> dict[str, float]:
        df = universe_df.copy()

        # 1. 专属大盘环境过滤器（Market Breadth）
        market_breadth = (df['ma20_deviation'] > 0).mean() if 'ma20_deviation' in df.columns else 0.5

        # 逆向风控：既然是超跌反弹，当大盘全线发飙（>0.7）时，说明根本没有全市场级别的超跌，此时出击容易接到高位衰竭股
        if market_breadth > 0.70:
            logger.info(f"[超跌反弹风控] {date.date()} 市场极度狂热 (宽度 {market_breadth:.2f})，放弃超跌交易，保持空仓")
            return {}

        # 大盘冰点是抄底最好的时机，因此 < 0.35 反而是我们的重仓区。
        max_total_weight = 0.95

        # 2. 基础流动性与排雷过滤
        if 'close' in df.columns:
            df = df[df['close'] >= 2.0]
        if 'isST' in df.columns:
            df = df[df['isST'] == 0]
        if 'list_days' in df.columns:
            df = df[df['list_days'] >= 120]

        # 3. 超卖因子筛选
        # - ma20_deviation < -10 即距离均线相差10%以上
        # - bb_pct_b < 0 即价格跌破布林带的 Lower Band
        # - rsi < 30 即 14 日 RSI 处于严重超卖区

        mask = pd.Series(True, index=df.index)

        if 'ma20_deviation' in df.columns:
            mask &= (df['ma20_deviation'] < -10)

        if 'bb_pct_b' in df.columns:
            mask &= (df['bb_pct_b'] < 0.05)   # 极度贴近或跌破下轨

        if 'rsi' in df.columns:
            mask &= (df['rsi'] < 30)

        candidates = df[mask].copy()

        if len(candidates) == 0:
            return {}

        # 4. 排序：按近期跌幅最深（momentum_5d 最低）并且偏离度最大的排
        if 'momentum_5d' in candidates.columns:
            candidates = candidates.sort_values(by='momentum_5d', ascending=True)

        selected = candidates.head(self.top_n)
        weight = max_total_weight / len(selected)
        weights = {sym: weight for sym in selected.index}

        logger.debug(
            f"[恐慌反弹] {date.date()} 宽度 {market_breadth:.2f} | 检出 {len(candidates)} 只超卖股 → 抄底 {len(weights)} 只"
        )
        return weights
