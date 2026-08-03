"""
摩擦与风险因子计算模组 (Friction & Risk Factors)

基于价格容量波动的资金异动/庄家博弈分析。

修订记录:
  - v1: 基础版本
  - v2: Amihud 加 log 变换；idio_vol 改为 CAPM 残差版本
"""
import pandas as pd
import numpy as np
from typing import Optional


def compute_amihud_illiquidity(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    计算 Amihud 非流动性因子（原始值）
    公式: mean(|return| / amount, window)
    - return: 小数形式（涨跌幅% / 100）
    - amount: 元
    """
    if '成交额' not in df.columns or '涨跌幅' not in df.columns:
        return pd.Series(np.nan, index=df.index)

    amount = df['成交额'].replace(0, np.nan)  # 成交额为0的日子标记为 NaN，不参与计算
    abs_return = df['涨跌幅'].abs() / 100.0

    illiquidity_daily = abs_return / amount
    return illiquidity_daily.rolling(window=window, min_periods=window // 2).mean()


def compute_amihud_log(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    计算 Amihud 非流动性因子（对数版本）
    公式: log(mean(|return| / amount, window))

    对数化的必要性:
      1. 原始 Amihud 值极端右偏（小盘股可达大盘股的 1000+ 倍）
      2. 原始值数量级极小（~1e-11），浮点运算不稳定
      3. 对数化后分布近似正态，截面回归系数稳定
    """
    raw = compute_amihud_illiquidity(df, window)
    # 对 0 和 NaN 做保护：先 clip 到极小正数再取 log
    return np.log(raw.clip(lower=1e-20))


def compute_turnover_volatility(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    计算换手率波动 (Turnover Volatility)
    公式: std(turnover_decimal, window)
    - turnover_decimal = turnover_pct / 100
    """
    if '换手率' not in df.columns:
        return pd.Series(np.nan, index=df.index)

    return (df['换手率'] / 100.0).rolling(window=window, min_periods=window // 2).std()


def compute_total_vol(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    总波动率 (Total Volatility)
    公式: std(return_decimal, window)

    注意: 这不是特质波动率！包含了系统性风险成分。
    仅在无法获取市场基准时作为 idio_vol 的降级替代。
    """
    if '涨跌幅' not in df.columns:
        return pd.Series(np.nan, index=df.index)

    return (df['涨跌幅'] / 100.0).rolling(window=window, min_periods=window // 2).std()


def compute_idio_vol(
    df: pd.DataFrame,
    market_returns: Optional[pd.Series] = None,
    window: int = 60,
    min_periods: int = 20,
) -> pd.Series:
    """
    特质波动率 (Idiosyncratic Volatility) — CAPM 残差版本

    方法:
      1. 对每只股票的日收益率序列，在滚动窗口内对市场收益率做 OLS 回归:
           r_i = α + β * r_market + ε
      2. 取残差 ε 的标准差作为特质波动率

    参数:
      df: 个股 DataFrame，必须含 '涨跌幅' 列（百分比形式）和 '日期' 列
      market_returns: 市场基准日收益率 Series（小数形式），index 为日期
                      若为 None，则降级为总波动率
      window: 回归窗口长度（默认60个交易日，约3个月）
      min_periods: 最少需要的样本数

    Returns:
      特质波动率 Series
    """
    if '涨跌幅' not in df.columns:
        return pd.Series(np.nan, index=df.index)

    stock_ret = df['涨跌幅'] / 100.0  # 转小数

    # 如果没有市场基准，降级为总波动率（并打标记）
    if market_returns is None:
        return compute_total_vol(df, window)

    # 将市场收益率按日期对齐到个股
    dates = pd.to_datetime(df['日期'])
    mkt_aligned = market_returns.reindex(dates).values

    # 滚动窗口 OLS 回归取残差 std
    idio = pd.Series(np.nan, index=df.index)

    stock_vals = stock_ret.values
    mkt_vals = mkt_aligned

    for i in range(min_periods, len(df)):
        start = max(0, i - window)
        y = stock_vals[start:i + 1]
        x = mkt_vals[start:i + 1]

        # 过滤 NaN
        mask = ~(np.isnan(y) | np.isnan(x))
        if mask.sum() < min_periods:
            continue

        y_clean = y[mask]
        x_clean = x[mask]

        # OLS: y = alpha + beta * x + epsilon
        # 用 numpy 最小二乘，避免引入 statsmodels 依赖
        X = np.column_stack([np.ones(len(x_clean)), x_clean])
        try:
            coeffs, _, _, _ = np.linalg.lstsq(X, y_clean, rcond=None)
            residuals = y_clean - X @ coeffs
            idio.iloc[i] = np.std(residuals, ddof=1)
        except np.linalg.LinAlgError:
            continue

    return idio


def build_market_returns(all_stock_dfs: dict[str, pd.DataFrame]) -> pd.Series:
    """
    从股票池构建市场等权日收益率序列（作为 CAPM 的市场代理变量）

    参数:
      all_stock_dfs: {symbol: DataFrame} 字典，每个 df 含 '日期' 和 '涨跌幅' 列

    Returns:
      市场等权日收益率 Series，index 为 datetime，值为小数形式
    """
    all_returns = []
    for symbol, df in all_stock_dfs.items():
        if df is None or df.empty or '涨跌幅' not in df.columns:
            continue
        ret = df[['日期', '涨跌幅']].copy()
        ret['日期'] = pd.to_datetime(ret['日期'])
        ret = ret.set_index('日期')
        ret = ret.rename(columns={'涨跌幅': symbol})
        all_returns.append(ret[symbol])

    if not all_returns:
        return pd.Series(dtype=float)

    ret_matrix = pd.concat(all_returns, axis=1)
    # 等权平均，除以100转小数
    market_ret = ret_matrix.mean(axis=1) / 100.0
    return market_ret


def add_friction_factors(
    df: pd.DataFrame,
    window: int = 20,
    market_returns: Optional[pd.Series] = None,
    idio_window: int = 60,
) -> pd.DataFrame:
    """
    将所有摩擦/风险类因子挂载到数据列上

    参数:
      df: 个股 DataFrame
      window: Amihud 和 turnover_vol 的滚动窗口
      market_returns: 市场基准日收益率（小数形式），用于 CAPM 残差计算
                      若为 None，idio_vol 降级为 total_vol
      idio_window: CAPM 回归的滚动窗口（默认60日）
    """
    df_out = df.copy()

    # Amihud 原始值 + 对数版本
    df_out['amihud'] = compute_amihud_illiquidity(df_out, window)
    df_out['amihud_log'] = compute_amihud_log(df_out, window)

    # 换手率波动
    df_out['turnover_vol'] = compute_turnover_volatility(df_out, window)

    # 总波动率（始终提供）
    df_out['total_vol'] = compute_total_vol(df_out, window)

    # 特质波动率（有市场基准时为 CAPM 残差版，否则降级为 total_vol）
    df_out['idio_vol'] = compute_idio_vol(
        df_out, market_returns=market_returns, window=idio_window
    )

    return df_out
