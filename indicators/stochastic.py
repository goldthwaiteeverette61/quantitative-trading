"""
Stochastic Oscillator — 随机指标 (KDJ)

由 George Lane 发明。衡量收盘价在一段时间内高低区间中的相对位置。

- %K > 80：超买区
- %K < 20：超卖区
- Elder 在三重滤网第二重将其作为震荡指标的备选
"""
import pandas as pd
from .moving_average import sma


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """
    计算 Stochastic Oscillator

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        k_period: %K 周期（默认 14）
        d_period: %D 周期（默认 3）

    Returns:
        (pct_k, pct_d) 两条线，范围 0-100
    """
    lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
    highest_high = high.rolling(window=k_period, min_periods=k_period).max()

    denominator = highest_high - lowest_low
    # 避免除零
    denominator = denominator.replace(0, float('nan'))

    pct_k = ((close - lowest_low) / denominator) * 100
    pct_d = sma(pct_k, d_period)

    return pct_k, pct_d
