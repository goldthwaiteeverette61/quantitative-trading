"""
冰点抄底 — 多持有期 × 市值分组胜率矩阵 (优化版)
=================================================
优化: 预先按日期建立字典索引，避免逐次全表扫描
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
import pandas as pd
import numpy as np
import json
import time

DB_CONFIG = dict(host='localhost', user='root', password='root123456', database='quant_trading')
OUTPUT_PATH = Path(__file__).parent.parent / 'web' / 'icepoint_full_matrix.json'

MA_PERIODS = [60, 120, 250]
THRESHOLD = 10.0
HOLD_DAYS = [(66, '3个月'), (132, '6个月'), (198, '9个月'), (250, '12个月')]
MV_BINS = [
    ('微盘 (<30亿)', 0, 3e9),
    ('小盘 (30~100亿)', 3e9, 1e11),
    ('中盘 (100~500亿)', 1e11, 5e11),
    ('大盘 (>500亿)', 5e11, float('inf')),
    ('全市场', 0, float('inf')),
]


def main():
    print("=" * 70)
    print("📊 冰点抄底 — 完整胜率矩阵 (优化版)")
    print("=" * 70)
    t0 = time.time()

    conn = pymysql.connect(**DB_CONFIG)
    df = pd.read_sql_query(
        "SELECT symbol, date, close, circ_mv FROM daily_kline WHERE date >= '2006-01-01' ORDER BY symbol, date",
        conn
    )
    conn.close()

    df['date'] = df['date'].astype(str)
    df['close'] = df['close'].astype(np.float32)
    df['circ_mv'] = pd.to_numeric(df['circ_mv'], errors='coerce')
    df = df.sort_values(['symbol', 'date'])
    n_stocks = df['symbol'].nunique()
    print(f"   ✅ {len(df):,} 条, {n_stocks} 只股票, 加载用时 {time.time()-t0:.0f}s")

    # ★ 核心优化: 预先按日期分组为字典
    t1 = time.time()
    print("   📦 预处理: 按日期建立索引...")
    date_groups = {}
    for date_str, grp in df.groupby('date'):
        date_groups[date_str] = grp[['symbol', 'close', 'circ_mv']].reset_index(drop=True)
    all_dates = sorted(date_groups.keys())
    date_to_idx = {d: i for i, d in enumerate(all_dates)}
    print(f"   ✅ 索引完成, {len(all_dates)} 个交易日, 用时 {time.time()-t1:.0f}s")

    all_results = []

    for ma_period in MA_PERIODS:
        ma_label = {60: '季线(60日)', 120: '半年线(120日)', 250: '年线(250日)'}[ma_period]
        t2 = time.time()
        print(f"\n📊 计算 {ma_label} 广度...")

        df['ma'] = df.groupby('symbol')['close'].transform(
            lambda x: x.rolling(ma_period, min_periods=ma_period).mean()
        )
        valid_mask = df['ma'].notna()
        df_valid = df[valid_mask]

        daily_breadth = df_valid.groupby('date').agg(
            total=('close', 'count'),
            above_count=('close', lambda x: ((df_valid.loc[x.index, 'close'] > df_valid.loc[x.index, 'ma']).sum()))
        )
        daily_breadth['breadth_pct'] = daily_breadth['above_count'] / daily_breadth['total'] * 100
        bs = daily_breadth['breadth_pct']

        trigger_dates = daily_breadth[(bs.shift(1) >= THRESHOLD) & (bs < THRESHOLD)].index.tolist()
        print(f"   触发日: {len(trigger_dates)} 次, 均线计算用时 {time.time()-t2:.0f}s")

        t3 = time.time()
        for hold_days, hold_label in HOLD_DAYS:
            for mv_label, mv_low, mv_high in MV_BINS:
                returns = []
                for trig_date in trigger_dates:
                    if trig_date not in date_to_idx:
                        continue
                    trig_idx = date_to_idx[trig_date]
                    fwd_idx = trig_idx + hold_days
                    if fwd_idx >= len(all_dates):
                        continue
                    fwd_date = all_dates[fwd_idx]

                    # ★ 直接从字典取，O(1)
                    day0 = date_groups[trig_date]
                    day1 = date_groups[fwd_date]

                    # 按市值过滤
                    if mv_high < float('inf'):
                        day0_filt = day0[(day0['circ_mv'] >= mv_low) & (day0['circ_mv'] < mv_high) & (day0['circ_mv'] > 0)]
                    else:
                        day0_filt = day0[(day0['circ_mv'] >= mv_low) & (day0['circ_mv'] > 0)]

                    if len(day0_filt) < 5:
                        continue

                    merged = day0_filt.merge(day1[['symbol', 'close']], on='symbol', how='inner', suffixes=('_t0', '_t1'))
                    if len(merged) < 5:
                        continue
                    ret = float((merged['close_t1'] / merged['close_t0'] - 1).median() * 100)
                    returns.append(ret)

                if not returns:
                    continue

                win = len([r for r in returns if r > 0])
                row = {
                    'ma_period': ma_period,
                    'ma_label': ma_label,
                    'hold_days': hold_days,
                    'hold_label': hold_label,
                    'mv_label': mv_label,
                    'triggers': len(returns),
                    'win_rate': round(win / len(returns) * 100, 1),
                    'median_return': round(float(np.median(returns)), 2),
                    'avg_return': round(float(np.mean(returns)), 2),
                    'best': round(float(max(returns)), 2),
                    'worst': round(float(min(returns)), 2),
                }
                all_results.append(row)

        print(f"   信号处理用时: {time.time()-t3:.0f}s")
        df.drop(columns=['ma'], inplace=True)

    # 打印 Top 15
    top15 = sorted(all_results, key=lambda x: (-x['win_rate'], -x['median_return']))[:15]
    print(f"\n{'=' * 90}")
    print(f"🏆 Top 15 最高胜率参数组合 (共测试 {len(all_results)} 组)")
    print(f"{'=' * 90}")
    for i, r in enumerate(top15):
        emoji = "🟢" if r['win_rate'] >= 65 else "🟡"
        print(f"  {emoji} #{i+1}: {r['ma_label']} + {r['hold_label']} + {r['mv_label']} → 胜率 {r['win_rate']:>5.1f}%  中位 {r['median_return']:>+7.2f}%  ({r['triggers']}次)")

    output = {
        'meta': {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S')},
        'results': all_results,
        'top15': top15,
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 写入: {OUTPUT_PATH}")
    print(f"⏱️ 总耗时: {time.time() - t0:.1f}s")


if __name__ == '__main__':
    main()
