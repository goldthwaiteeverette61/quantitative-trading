"""
动能突破 V1 — 信号过滤条件

六条件联合碰撞：
    1. 突破 20 日新高
    2. 爆量 3-5 倍
    3. 涨幅 > min_pct_chg
    4. 阳线
    5. 昨日未突破（首日起爆）
    6. 昨日量能静默
"""

import pandas as pd


def v1_1_signal_filter(merged: pd.DataFrame, pre_close_col: str, params: dict) -> pd.DataFrame:
    """
    V1 六条件向量化碰撞。

    参数:
        merged: 快照与阈值合并后的 DataFrame
        pre_close_col: 昨收价列名（自动处理列名冲突）
        params: 策略参数字典（从 task_state 提取）

    返回:
        满足全部条件的信号 DataFrame
    """
    min_pct_chg = params.get('min_pct_chg', 5.0)
    vol_threshold = params.get('vol_threshold', 3.0)
    max_vol_threshold = params.get('max_vol_threshold', 5.0)
    yest_vol_max_ratio = params.get('yest_vol_max_ratio', 2.0)

    # ===== 六条件向量化碰撞 =====
    cond1 = merged['price'] > merged['high_20d']          # 突破 20 日新高
    cond2_lo = merged['volume'] > merged['vol_burst']     # 爆量 > 3倍
    cond2_hi = merged['volume'] < merged['vol_burst'] * (max_vol_threshold / vol_threshold)  # 天量 < 5倍
    cond3 = merged['pct_chg'] > min_pct_chg               # 涨幅
    cond4 = merged['price'] > merged['today_open']        # 阳线

    # 首日起爆验证
    cond_fresh = merged[pre_close_col] < merged['high_20d']
    cond_quiet = merged['yest_volume'] < merged['avg_vol_20d'] * yest_vol_max_ratio

    # 额外过滤
    cond_valid1 = merged['price'] > 0
    cond_valid2 = merged['today_open'] > 0

    signals = merged[
        cond1 & cond2_lo & cond2_hi & cond3 & cond4 &
        cond_fresh & cond_quiet &
        cond_valid1 & cond_valid2
    ].copy()

        # V1-1 新增防飞刀
    signals = signals[signals['vwap_dev'] <= params['max_vwap_dev']]
    signals = signals[signals['turnover'] <= params['max_turnover_pct']]
    return signals


def v1_1_buy_remark(row: pd.Series, params: dict) -> str:
    """生成 V1 买入备注"""
    price = row['price']
    pct = row['pct_chg']
    vr = row.get('vol_ratio_live', 0)
    return f"动能指标: 现价¥{price:.2f}(+{pct:.1f}%), 量比{vr:.1f}x, 突破20日高"
