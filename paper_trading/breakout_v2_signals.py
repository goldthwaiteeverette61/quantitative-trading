"""
动能突破 V2 — 信号过滤条件（VWAP + 换手率增强）

八条件联合碰撞：
    1. 突破 20 日新高
    2. 爆量 3-5 倍
    3. 涨幅 > min_pct_chg
    4. 阳线
    5. VWAP 偏离 > min_vwap_dev
    6. 换手率 > min_turnover_pct
    7. 昨日未突破（首日起爆）
    8. 昨日量能静默
"""

import pandas as pd
from datetime import datetime
from loguru import logger


def v2_signal_filter(merged: pd.DataFrame, pre_close_col: str, params: dict) -> pd.DataFrame:
    """
    V2 八条件向量化碰撞（在 V1 基础上增加 VWAP 偏离 + 换手率）。

    参数:
        merged: 快照与阈值合并后的 DataFrame
        pre_close_col: 昨收价列名
        params: 策略参数字典

    返回:
        满足全部条件的信号 DataFrame
    """
    min_pct_chg = params.get('min_pct_chg', 5.0)
    vol_threshold = params.get('vol_threshold', 3.0)
    max_vol_threshold = params.get('max_vol_threshold', 5.0)
    yest_vol_max_ratio = params.get('yest_vol_max_ratio', 2.0)
    min_vwap_dev = params.get('min_vwap_dev', 0.02)
    min_turnover_pct = params.get('min_turnover_pct', 5.0)
    task_id = params.get('task_id', '')

    # ===== 八条件向量化碰撞 =====
    cond1 = merged['price'] > merged['high_20d']          # 突破 20 日新高
    cond2_lo = merged['volume'] > merged['vol_burst']     # 爆量 > 3倍
    cond2_hi = merged['volume'] < merged['vol_burst'] * (max_vol_threshold / vol_threshold)  # 天量 < 5倍
    cond3 = merged['pct_chg'] > min_pct_chg               # 涨幅
    cond4 = merged['price'] > merged['today_open']        # 阳线

    # V2 新增条件5：VWAP 偏离度
    merged['vwap'] = merged['amount'] / merged['volume']
    merged['vwap_dev'] = (merged['price'] - merged['vwap']) / merged['vwap']
    cond5_vwap = merged['vwap_dev'] > min_vwap_dev

    # V2 新增条件6：换手率
    merged['float_shares'] = merged['circ_mv'] / merged['price']
    merged['turnover_pct'] = merged['volume'] / merged['float_shares'] * 100
    cond6_turnover = merged['turnover_pct'] > min_turnover_pct

    # 首日起爆验证
    cond_fresh = merged[pre_close_col] < merged['high_20d']
    cond_quiet = merged['yest_volume'] < merged['avg_vol_20d'] * yest_vol_max_ratio

    # 额外过滤
    cond_valid1 = merged['price'] > 0
    cond_valid2 = merged['today_open'] > 0
    cond_valid3 = merged['circ_mv'] > 0

    signals = merged[
        cond1 & cond2_lo & cond2_hi & cond3 & cond4 &
        cond5_vwap & cond6_turnover &
        cond_fresh & cond_quiet &
        cond_valid1 & cond_valid2 & cond_valid3
    ].copy()

    # --- DEBUG LOGGING ---
    now_m = datetime.now().minute
    if now_m % 5 == 0:
        logger.info(f"[动能突破V2 {task_id}] 过滤统计: 总量={len(merged)}, "
                    f"突破20日={cond1.sum()}, 爆量下限={cond2_lo.sum()}, 涨幅={cond3.sum()}, "
                    f"阳线={cond4.sum()}, VWAP偏离>{min_vwap_dev*100:.0f}%={cond5_vwap.sum()}, "
                    f"换手率>{min_turnover_pct}%={cond6_turnover.sum()}, "
                    f"首日={cond_fresh.sum()}, 昨缩量={cond_quiet.sum()}")

    return signals


def v2_buy_remark(row: pd.Series, params: dict) -> str:
    """生成 V2 买入备注"""
    price = row['price']
    pct = row['pct_chg']
    vr = row.get('vol_ratio_live', 0)
    return (f"动能V2: ¥{price:.2f}(+{pct:.1f}%), 量比{vr:.1f}x, "
            f"VWAP偏离{row.get('vwap_dev', 0)*100:.1f}%, "
            f"换手{row.get('turnover_pct', 0):.1f}%")
