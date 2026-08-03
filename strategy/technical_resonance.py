"""
五维共振技术选股策略 — Technical Resonance

核心理念：任何单一技术指标都不可靠，但当 趋势/动量/波动/量价/形态
五个维度同时确认时，胜率会显著提升。

策略变体：
  - TechResonanceNormal: 通用版（中证1000等正常流动性池子）
  - TechResonanceMicroCap: 微盘版（调整权重偏向散户行为特征）
"""
import pandas as pd
import numpy as np
from loguru import logger

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


# ============================================================
#  评分引擎（核心）
# ============================================================

def score_trend(row: pd.Series) -> float:
    """
    ① 趋势方向（0~20 分）
    多头排列 + 站在趋势之上 + ADX 趋势强度
    """
    s = 0.0

    # EMA 多头排列: ema5 > ema10 > ema20
    ema5, ema10, ema20 = row.get('ema5', 0), row.get('ema10', 0), row.get('ema20', 0)
    if ema5 > 0 and ema10 > 0 and ema20 > 0:
        if ema5 > ema10 > ema20:
            s += 10  # 完美多头排列
        elif ema5 > ema20:
            s += 5   # 不完美但大方向对

    # 收盘价 > EMA20
    close = row.get('close', 0)
    if close > 0 and ema20 > 0 and close > ema20:
        s += 5

    # ADX > 25 趋势明确
    adx = row.get('adx', 0)
    if adx > 25:
        s += 5
    elif adx > 20:
        s += 2

    return min(s, 20)


def score_momentum(row: pd.Series) -> float:
    """
    ② 动量加速（0~20 分）
    MACD 柱状图 + RSI 位置 + ROC 短期动量
    """
    s = 0.0

    # MACD 柱 > 0 且递增（3日斜率 > 0）
    macd_hist = row.get('macd_hist', 0)
    macd_hist_slope = row.get('macd_hist_slope', 0)
    if macd_hist > 0 and macd_hist_slope > 0:
        s += 8   # 多头加速
    elif macd_hist > 0:
        s += 4   # 多头但减速

    # RSI 在 40~70（有空间但不超买）
    rsi = row.get('rsi', 50)
    if 40 <= rsi <= 70:
        s += 6
    elif 30 <= rsi < 40 or 70 < rsi <= 80:
        s += 3

    # 5日 ROC > 0（短期正动量）
    roc5 = row.get('momentum_5d', 0)
    if roc5 > 0:
        s += 6
    elif roc5 > -2:
        s += 2

    return min(s, 20)


def score_volatility(row: pd.Series) -> float:
    """
    ③ 波动位置（0~20 分）
    布林带位置 + ATR 突破
    """
    s = 0.0

    # 布林带 %B：0.3~0.7 中轨附近（上方有空间，又不是追高）
    bb_pct_b = row.get('bb_pct_b', 0.5)
    if 0.3 <= bb_pct_b <= 0.7:
        s += 10
    elif 0.2 <= bb_pct_b < 0.3:
        s += 6   # 偏下轨，可能要反弹
    elif 0.7 < bb_pct_b <= 0.85:
        s += 4   # 偏上轨，动量尚可

    # ATR 突破：近5日 ATR 收缩后今日放大
    atr_breakout = row.get('atr_breakout', 0)
    if atr_breakout > 0:
        s += 10
    else:
        # 如果 ATR 在收缩中（蓄势），给部分分
        atr_compress = row.get('atr_compress', 0)
        if atr_compress > 0:
            s += 4

    return min(s, 20)


def score_volume(row: pd.Series) -> float:
    """
    ④ 量价验证（0~20 分）
    放量确认 + OBV 趋势 + 换手率甜区
    """
    s = 0.0

    # 量比（vs 5日均量）且收阳
    vol_ratio = row.get('vol_ratio', 1.0)
    is_bullish_candle = row.get('close', 0) > row.get('open', 0) if row.get('open', 0) > 0 else False
    if vol_ratio > 1.2 and is_bullish_candle:
        s += 10  # 放量阳线 = 主力介入
    elif vol_ratio > 1.0 and is_bullish_candle:
        s += 5

    # OBV 5日斜率 > 0（资金持续流入）
    obv_slope = row.get('obv_slope', 0)
    if obv_slope > 0:
        s += 5

    # 换手率在甜区 2%~8%
    turnover = row.get('turnover', 0)
    if 2.0 <= turnover <= 8.0:
        s += 5
    elif 1.0 <= turnover < 2.0 or 8.0 < turnover <= 12.0:
        s += 2

    return min(s, 20)


def score_pattern(row: pd.Series) -> float:
    """
    ⑤ 形态信号（0~20 分）
    N日新高突破 + 均线金叉
    """
    s = 0.0

    # 创20日新高
    is_20d_high = row.get('is_20d_high', 0)
    if is_20d_high:
        s += 10

    # 近5日内有 EMA5/EMA20 金叉
    ema_golden_cross = row.get('ema_golden_cross', 0)
    if ema_golden_cross:
        s += 10

    return min(s, 20)


# ============================================================
#  微盘版评分（调整权重和阈值）
# ============================================================

def score_volume_microcap(row: pd.Series) -> float:
    """微盘版量价验证：放宽换手率阈值，更重视放量信号"""
    s = 0.0

    vol_ratio = row.get('vol_ratio', 1.0)
    is_bullish = row.get('close', 0) > row.get('open', 0) if row.get('open', 0) > 0 else False

    # 微盘股放量信号更灵敏: 1.5 倍即算放量
    if vol_ratio > 1.5 and is_bullish:
        s += 12  # 加权更高（散户场里量能更关键）
    elif vol_ratio > 1.2 and is_bullish:
        s += 7

    # OBV
    obv_slope = row.get('obv_slope', 0)
    if obv_slope > 0:
        s += 4

    # 微盘股换手率甜区更高: 3%~15%
    turnover = row.get('turnover', 0)
    if 3.0 <= turnover <= 15.0:
        s += 4
    elif 1.5 <= turnover < 3.0:
        s += 2

    return min(s, 20)


def score_momentum_microcap(row: pd.Series) -> float:
    """微盘版动量：RSI 阈值放宽（小盘波动本来就大）"""
    s = 0.0

    macd_hist = row.get('macd_hist', 0)
    macd_hist_slope = row.get('macd_hist_slope', 0)
    if macd_hist > 0 and macd_hist_slope > 0:
        s += 8
    elif macd_hist > 0:
        s += 4

    # 微盘 RSI 可以更宽容：35~75
    rsi = row.get('rsi', 50)
    if 35 <= rsi <= 75:
        s += 6
    elif 25 <= rsi < 35:
        s += 4  # 超跌反弹机会更高

    roc5 = row.get('momentum_5d', 0)
    if roc5 > 0:
        s += 6
    elif roc5 > -3:
        s += 2

    return min(s, 20)


# ============================================================
#  策略类
# ============================================================

class TechResonanceNormal(PortfolioStrategy):
    """
    五维共振 · 通用版

    适用于中证1000等正常流动性池子。
    五维均等权重（各20分），总分≥60进入候选池，按分数排序取 Top N。
    """

    def __init__(self, top_n: int = 20, min_score: int = 55, rebalance_freq: str = "weekly"):
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=top_n))
        self.top_n = top_n
        self.min_score = min_score

    @property
    def name(self) -> str:
        return "五维共振_通用版"

    @property
    def category(self) -> str:
        return "技术"

    @property
    def description(self) -> str:
        return (f"趋势/动量/波动/量价/形态五维共振评分，"
                f"≥{self.min_score}分进入候选池，取Top{self.top_n}等权持有，"
                f"周度调仓。适用于中证1000等中等流动性市场。")

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(self, date: pd.Timestamp, universe_df: pd.DataFrame) -> dict[str, float]:
        df = universe_df.copy()

        # 大盘环境过滤器（Market Breadth）
        market_breadth = (df['ma20_deviation'] > 0).mean() if 'ma20_deviation' in df.columns else 1.0
        if market_breadth < 0.35:
            logger.info(f"[五维共振·通用风控] {date.date()} 大盘极度弱势 (宽度 {market_breadth:.2f} < 0.35)，强制空仓防守")
            return {}

        max_total_weight = 0.95
        if market_breadth < 0.50:
            max_total_weight = 0.45
            logger.info(f"[五维共振·通用风控] {date.date()} 大盘偏弱 (宽度 {market_breadth:.2f} < 0.50)，降仓至 {max_total_weight}")

        # 基础过滤
        if 'close' in df.columns:
            df = df[df['close'] >= 3.0]
        if 'isST' in df.columns:
            df = df[df['isST'] == 0]
        if 'list_days' in df.columns:
            df = df[df['list_days'] >= 120]  # 至少上市半年

        # 五维打分
        df['s_trend'] = df.apply(score_trend, axis=1)
        df['s_momentum'] = df.apply(score_momentum, axis=1)
        df['s_volatility'] = df.apply(score_volatility, axis=1)
        df['s_volume'] = df.apply(score_volume, axis=1)
        df['s_pattern'] = df.apply(score_pattern, axis=1)
        df['total_score'] = df[['s_trend', 's_momentum', 's_volatility', 's_volume', 's_pattern']].sum(axis=1)

        # 过滤低分
        candidates = df[df['total_score'] >= self.min_score].sort_values('total_score', ascending=False)

        if len(candidates) == 0:
            logger.info(f"[五维共振·通用] {date.date()} 无股票达到 {self.min_score} 分阈值")
            return {}

        selected = candidates.head(self.top_n)
        weight = max_total_weight / len(selected)
        weights = {sym: weight for sym in selected.index}

        top3 = selected.head(3)
        logger.debug(
            f"[五维共振·通用] {date.date()} 候选{len(candidates)}只 → 持仓{len(weights)}只 "
            f"| Top3: {', '.join(f'{s}({int(r.total_score)}分)' for s, r in top3.iterrows())}"
        )
        return weights


class TechResonanceMicroCap(PortfolioStrategy):
    """
    五维共振 · 微盘散户版

    针对小微盘股（散户为主的市场），调整如下：
    - 市值上限 50 亿
    - 量价维度加权（散户场里资金流向更关键）
    - RSI 阈值放宽（小盘波动大是常态）
    - 换手率甜区调高（微盘本身换手高）
    """

    def __init__(self, top_n: int = 20, min_score: int = 50,
                 max_market_cap: float = 50.0, rebalance_freq: str = "weekly"):
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=top_n))
        self.top_n = top_n
        self.min_score = min_score
        self.max_market_cap = max_market_cap

    @property
    def name(self) -> str:
        return "五维共振_微盘版"

    @property
    def category(self) -> str:
        return "技术"

    @property
    def description(self) -> str:
        return (f"针对小微盘股（市值<{self.max_market_cap}亿）的五维共振策略。"
                f"调整量价权重和阈值以适应散户为主的交易特征，"
                f"≥{self.min_score}分进入候选池，Top{self.top_n}等权持有。")

    @property
    def author(self) -> str:
        return "量化系统"

    def get_target_weights(self, date: pd.Timestamp, universe_df: pd.DataFrame) -> dict[str, float]:
        df = universe_df.copy()

        # 大盘环境过滤器（Market Breadth）
        market_breadth = (df['ma20_deviation'] > 0).mean() if 'ma20_deviation' in df.columns else 1.0
        if market_breadth < 0.35:
            logger.info(f"[五维共振·微盘风控] {date.date()} 大盘极度弱势 (宽度 {market_breadth:.2f} < 0.35)，强制空仓防守")
            return {}

        max_total_weight = 0.95
        if market_breadth < 0.50:
            max_total_weight = 0.45
            logger.info(f"[五维共振·微盘风控] {date.date()} 大盘偏弱 (宽度 {market_breadth:.2f} < 0.50)，降仓至 {max_total_weight}")

        # 基础过滤（微盘版）
        if 'close' in df.columns:
            df = df[df['close'] >= 2.0]  # 微盘允许更低价
        if 'isST' in df.columns:
            df = df[df['isST'] == 0]
        if 'list_days' in df.columns:
            df = df[df['list_days'] >= 90]  # 微盘上市3个月即可
        if 'market_cap' in df.columns:
            df = df[(df['market_cap'] > 0) & (df['market_cap'] <= self.max_market_cap)]

        # 五维打分（量价和动量用微盘特化版本）
        df['s_trend'] = df.apply(score_trend, axis=1)
        df['s_momentum'] = df.apply(score_momentum_microcap, axis=1)
        df['s_volatility'] = df.apply(score_volatility, axis=1)
        df['s_volume'] = df.apply(score_volume_microcap, axis=1)
        df['s_pattern'] = df.apply(score_pattern, axis=1)
        df['total_score'] = df[['s_trend', 's_momentum', 's_volatility', 's_volume', 's_pattern']].sum(axis=1)

        candidates = df[df['total_score'] >= self.min_score].sort_values('total_score', ascending=False)

        if len(candidates) == 0:
            logger.info(f"[五维共振·微盘] {date.date()} 无股票达到 {self.min_score} 分阈值")
            return {}

        selected = candidates.head(self.top_n)
        weight = max_total_weight / len(selected)
        weights = {sym: weight for sym in selected.index}

        top3 = selected.head(3)
        logger.debug(
            f"[五维共振·微盘] {date.date()} 候选{len(candidates)}只 → 持仓{len(weights)}只 "
            f"| Top3: {', '.join(f'{s}({int(r.total_score)}分)' for s, r in top3.iterrows())}"
        )
        return weights
