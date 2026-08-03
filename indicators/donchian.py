"""
Donchian Channel — 唐奇安通道

由 Richard Donchian 发明，海龟交易法的核心指标。

- 上轨 = N 日最高价
- 下轨 = N 日最低价
- 中轨 = (上轨 + 下轨) / 2

海龟交易法则：
- 价格突破 20 日上轨 → 入场做多
- 价格跌破 10 日下轨 → 出场
"""
import pandas as pd
from dataclasses import dataclass


@dataclass
class DonchianResult:
    """Donchian 通道结果"""
    upper: pd.Series    # 上轨
    lower: pd.Series    # 下轨
    middle: pd.Series   # 中轨


def donchian_channel(
    high: pd.Series,
    low: pd.Series,
    period: int = 20,
) -> DonchianResult:
    """
    计算 Donchian 通道

    Args:
        high: 最高价序列
        low: 最低价序列
        period: 回看周期（默认 20）

    Returns:
        DonchianResult 包含上轨、下轨、中轨
    """
    # 使用前 N 日（不含当日）的最高价和最低价
    upper = high.rolling(window=period, min_periods=period).max().shift(1)
    lower = low.rolling(window=period, min_periods=period).min().shift(1)
    middle = (upper + lower) / 2

    return DonchianResult(upper=upper, lower=lower, middle=middle)
