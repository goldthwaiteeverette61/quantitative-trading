"""
移动平均线 — SMA / EMA

- SMA：简单移动平均，等权计算
- EMA：指数移动平均，近期权重更高，对价格变化更敏感
"""
import pandas as pd
import numpy as np


def sma(series: pd.Series, period: int) -> pd.Series:
    """
    简单移动平均线

    Args:
        series: 价格序列
        period: 周期

    Returns:
        SMA 序列
    """
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """
    指数移动平均线（Wilder 平滑法兼容）

    Args:
        series: 价格序列
        period: 周期

    Returns:
        EMA 序列
    """
    return series.ewm(span=period, adjust=False).mean()
