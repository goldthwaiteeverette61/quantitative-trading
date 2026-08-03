"""
Force Index — 强力指数

由 Alexander Elder 发明。结合价格变动和成交量，衡量多空力量的真实强度。

- Force Index = (今日收盘 - 昨日收盘) × 今日成交量
- 2 日 EMA 平滑后用于短期分析（三重滤网第二重）
- 13 日 EMA 平滑后用于中期趋势确认

在三重滤网中：
- 上升趋势 + Force Index(2) < 0 → 买入机会（短期回调但主力未撤）
- 下降趋势 + Force Index(2) > 0 → 卖出机会（短期反弹但力量有限）
"""
import pandas as pd
from .moving_average import ema


def force_index(
    close: pd.Series,
    volume: pd.Series,
    period: int = 2,
) -> pd.Series:
    """
    计算强力指数

    Args:
        close: 收盘价序列
        volume: 成交量序列
        period: EMA 平滑周期（默认 2，用于短期；13 用于中期）

    Returns:
        Force Index 序列
    """
    raw_force = close.diff() * volume
    return ema(raw_force, period)
