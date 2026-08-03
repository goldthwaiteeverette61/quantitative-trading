"""
动能突破 V3 — 信号过滤条件（ATR 自适应突破）

七条件联合碰撞：
    1. 突破 20 日新高
    2. 爆量 3-5 倍
    3. ATR 自适应突破：(price - pre_close) > K × ATR(20)
    4. 阳线
    5. 涨幅保底：pct_chg > min_pct_floor
    6. 昨日未突破（首日起爆）
    7. 昨日量能静默
"""

import pandas as pd


def v3_1_signal_filter(merged: pd.DataFrame, pre_close_col: str, params: dict) -> pd.DataFrame:
    """
    V3 七条件向量化碰撞（ATR 自适应替代固定涨幅门槛）。

    参数:
        merged: 快照与阈值合并后的 DataFrame
        pre_close_col: 昨收价列名
        params: 策略参数字典

    返回:
        满足全部条件的信号 DataFrame
    """
    vol_threshold = params.get('vol_threshold', 3.0)
    max_vol_threshold = params.get('max_vol_threshold', 5.0)
    yest_vol_max_ratio = params.get('yest_vol_max_ratio', 2.0)
    atr_multiplier = params.get('atr_multiplier', 1.5)
    min_pct_floor = params.get('min_pct_floor', 1.0)

    # ===== 七条件向量化碰撞 =====
    cond1 = merged['price'] > merged['high_20d']          # 突破 20 日新高
    cond2_lo = merged['volume'] > merged['vol_burst']     # 爆量 > 3倍
    cond2_hi = merged['volume'] < merged['vol_burst'] * (max_vol_threshold / vol_threshold)  # 天量 < 5倍

    # V3 核心改造：ATR 自适应突破门槛
    pre_close_live_col = 'pre_close_live' if 'pre_close_live' in merged.columns else 'pre_close'
    price_move = merged['price'] - merged[pre_close_live_col]
    atr_threshold = atr_multiplier * merged['atr_20d']
    cond3_atr = price_move > atr_threshold                # 涨幅 > K × ATR(20)
    cond3_floor = merged['pct_chg'] > min_pct_floor       # 保底：至少涨 1%

    cond4 = merged['price'] > merged['today_open']        # 阳线

    # 首日起爆验证
    cond_fresh = merged[pre_close_col] < merged['high_20d']
    cond_quiet = merged['yest_volume'] < merged['avg_vol_20d'] * yest_vol_max_ratio

    cond_valid1 = merged['price'] > 0
    cond_valid2 = merged['today_open'] > 0
    cond_valid3 = merged['atr_20d'] > 0

    signals = merged[
        cond1 & cond2_lo & cond2_hi &
        cond3_atr & cond3_floor &
        cond4 &
        cond_fresh & cond_quiet &
        cond_valid1 & cond_valid2 & cond_valid3
    ].copy()

        # V3-1 新增防飞刀上限
    signals = signals[signals['vwap_dev'] <= params['max_vwap_dev']]
    signals = signals[signals['turnover'] <= params['max_turnover_pct']]
    return signals


def v3_1_buy_remark(row: pd.Series, params: dict) -> str:
    """生成 V3 买入备注"""
    price = row['price']
    pct = row['pct_chg']
    vr = row.get('vol_ratio_live', 0)
    atr_val = row.get('atr_20d', 0)
    atr_multiplier = params.get('atr_multiplier', 1.5)
    return (f"动能V3(ATR): ¥{price:.2f}(+{pct:.1f}%), 量比{vr:.1f}x, "
            f"ATR={atr_val:.2f} ×{atr_multiplier}={atr_val*atr_multiplier:.2f}")
