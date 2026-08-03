"""
RSI — 相对强弱指数

由 J. Welles Wilder 发明。衡量价格变动的速度和幅度。

- RSI > 70：超买区（群体过度乐观）
- RSI < 30：超卖区（群体过度悲观）
- Elder 在三重滤网第二重中使用 RSI 识别回调
"""
import pandas as pd
import numpy as np


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    计算 RSI（Wilder 平滑法）

    使用 Wilder 指数平滑（等价于 alpha = 1/period），
    这是 RSI 的原始定义，与多数专业平台一致。

    Args:
        series: 价格序列（通常为收盘价）
        period: 计算周期（默认 14）

    Returns:
        RSI 序列（0-100）
    """
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Wilder 平滑 = EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))

    return result
