"""
ATR — 平均真实波动范围

由 J. Welles Wilder 发明。衡量市场波动性/焦虑程度。

- ATR 大：市场波动剧烈
- ATR 小：市场平静
- 用于动态止损（Elder: 止损 = 入场价 - 2×ATR）
- 海龟交易法用 ATR 计算仓位单位
"""
import pandas as pd
import numpy as np
from .moving_average import ema


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """
    计算 True Range

    TR = max(
        当日最高 - 当日最低,
        |当日最高 - 昨日收盘|,
        |当日最低 - 昨日收盘|
    )
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    计算 ATR（Wilder 平滑）

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        period: 计算周期（默认 14）

    Returns:
        ATR 序列
    """
    tr = true_range(high, low, close)
    # Wilder 平滑 = EMA with alpha = 1/period
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
