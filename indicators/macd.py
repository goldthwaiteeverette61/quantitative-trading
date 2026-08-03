"""
MACD — 平滑异同移动平均线

由 Gerald Appel 发明，Elder 在三重滤网中用其柱状图斜率判断趋势动量。

- MACD 线 = 快线 EMA - 慢线 EMA
- 信号线 = MACD 线的 EMA
- 柱状图 = MACD 线 - 信号线（核心：柱状图的方向比数值更重要）
"""
import pandas as pd
from dataclasses import dataclass
from .moving_average import ema


@dataclass
class MACDResult:
    """MACD 计算结果"""
    macd_line: pd.Series       # MACD 线
    signal_line: pd.Series     # 信号线
    histogram: pd.Series       # 柱状图
    hist_slope: pd.Series      # 柱状图斜率（Elder 核心判断工具）


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> MACDResult:
    """
    计算 MACD

    Args:
        series: 收盘价序列
        fast: 快线周期（默认 12）
        slow: 慢线周期（默认 26）
        signal: 信号线周期（默认 9）

    Returns:
        MACDResult 包含 macd_line, signal_line, histogram, hist_slope
    """
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)

    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line

    # 柱状图斜率：当前柱 - 前一柱
    hist_slope = histogram.diff()

    return MACDResult(
        macd_line=macd_line,
        signal_line=signal_line,
        histogram=histogram,
        hist_slope=hist_slope,
    )
