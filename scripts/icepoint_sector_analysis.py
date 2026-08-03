"""
冰点抄底时谁涨得最多？— 按市值分解 (修正版)
==========================================
circ_mv 单位是【元】，不是万元
微盘: <30亿元(3e9) / 小盘: 30~100亿(3e9~1e11) / 中盘: 100~500亿(1e11~5e11) / 大盘: >500亿(5e11)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
import pandas as pd
import numpy as np
import json
import time

DB_HOST = 'localhost'
DB_USER = 'root'
DB_PASS = 'root123456'
DB_NAME = 'quant_trading'

OUTPUT_PATH = Path(__file__).parent.parent / 'web' / 'icepoint_sector_data.json'

MA_PERIOD = 120
THRESHOLD = 10.0


def main():
    print("=" * 70)
    print("🔍 冰点抄底时谁涨得最多？— 市值分解 (修正版)")
    print("=" * 70)
    t0 = time.time()

    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)
    query = "SELECT symbol, date, close, circ_mv FROM daily_kline WHERE date >= '2006-01-01' ORDER BY symbol, date"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = df['date'].astype(str)
    df['close'] = df['close'].astype(np.float32)
    df['circ_mv'] = pd.to_numeric(df['circ_mv'], errors='coerce')
    df = df.sort_values(['symbol', 'date'])
    print(f"   ✅ {len(df):,} 条, {df['symbol'].nunique()} 只股票")

    # 计算广度
    print("\n📊 计算 120 日均线广度...")
    df['ma'] = df.groupby('symbol')['close'].transform(
        lambda x: x.rolling(MA_PERIOD, min_periods=MA_PERIOD).mean()
    )
    df['above'] = (df['close'] > df['ma']).astype(np.float32)
    valid = df.dropna(subset=['ma'])

    daily_breadth = valid.groupby('date').agg(
        total=('above', 'count'), above_count=('above', 'sum')
    )
    daily_breadth['breadth_pct'] = daily_breadth['above_count'] / daily_breadth['total'] * 100
    breadth_series = daily_breadth['breadth_pct']

    # 首次跌穿
    prev_above = breadth_series.shift(1) >= THRESHOLD
    curr_below = breadth_series < THRESHOLD
    trigger_dates = daily_breadth[prev_above & curr_below].index.tolist()
    print(f"   触发日: {len(trigger_dates)} 次")

    all_dates = sorted(valid['date'].unique())
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    # 市值分桶 (单位: 元)
    mv_bins = [
        ('微盘 (<30亿)', 0, 3e9),
        ('小盘 (30~100亿)', 3e9, 1e11),
        ('中盘 (100~500亿)', 1e11, 5e11),
        ('大盘 (>500亿)', 5e11, float('inf')),
    ]

    results_by_mv = {label: [] for label, _, _ in mv_bins}
    results_all = []

    for trig_date in trigger_dates:
        if trig_date not in date_to_idx:
            continue
        trig_idx = date_to_idx[trig_date]
        fwd_idx = trig_idx + 66
        if fwd_idx >= len(all_dates):
            continue
        fwd_date = all_dates[fwd_idx]

        day_data = valid[valid['date'] == trig_date][['symbol', 'close', 'circ_mv']].copy()
        day_data = day_data.rename(columns={'close': 'close_t0', 'circ_mv': 'mv_t0'})
        day_data = day_data[day_data['mv_t0'] > 0]

        fwd_data = valid[valid['date'] == fwd_date][['symbol', 'close']].copy()
        fwd_data = fwd_data.rename(columns={'close': 'close_t1'})

        merged = day_data.merge(fwd_data, on='symbol', how='inner')
        merged['ret_3m'] = (merged['close_t1'] / merged['close_t0'] - 1) * 100

        if len(merged) == 0:
            continue

        all_ret = float(merged['ret_3m'].median())
        results_all.append({'date': trig_date, 'median_return': round(all_ret, 2), 'count': len(merged)})

        for label, low, high in mv_bins:
            subset = merged[(merged['mv_t0'] >= low) & (merged['mv_t0'] < high)]
            if len(subset) < 5:
                continue
            med = float(subset['ret_3m'].median())
            results_by_mv[label].append({
                'date': trig_date,
                'count': len(subset),
                'median_return': round(med, 2),
                'mean_return': round(float(subset['ret_3m'].mean()), 2),
                'q25': round(float(subset['ret_3m'].quantile(0.25)), 2),
                'q75': round(float(subset['ret_3m'].quantile(0.75)), 2),
            })

    # 汇总
    print("\n" + "=" * 70)
    print("📋 冰点抄底 3 个月收益 — 按市值分组")
    print("=" * 70)

    summary = {}
    for label, _, _ in mv_bins:
        entries = results_by_mv[label]
        if not entries:
            summary[label] = {'periods': 0, 'win_rate': 0, 'avg_median_return': 0, 'overall_median': 0}
            print(f"   {label:<20} 无数据")
            continue
        medians = [e['median_return'] for e in entries]
        win_count = len([m for m in medians if m > 0])
        summary[label] = {
            'periods': len(entries),
            'win_rate': round(win_count / len(entries) * 100, 1),
            'avg_median_return': round(float(np.mean(medians)), 2),
            'overall_median': round(float(np.median(medians)), 2),
            'best': round(float(max(medians)), 2),
            'worst': round(float(min(medians)), 2),
        }
        print(f"   {label:<20} 样本={len(entries):<4} 胜率={summary[label]['win_rate']:>5.1f}%  中位收益={summary[label]['overall_median']:>+7.2f}%  平均={summary[label]['avg_median_return']:>+7.2f}%  最佳={summary[label]['best']:>+7.2f}%  最差={summary[label]['worst']:>+7.2f}%")

    all_medians = [e['median_return'] for e in results_all]
    all_win = len([m for m in all_medians if m > 0])
    summary['全市场等权'] = {
        'periods': len(results_all),
        'win_rate': round(all_win / len(results_all) * 100, 1) if results_all else 0,
        'avg_median_return': round(float(np.mean(all_medians)), 2) if all_medians else 0,
        'overall_median': round(float(np.median(all_medians)), 2) if all_medians else 0,
    }
    print(f"\n   {'全市场等权':<20} 样本={len(results_all):<4} 胜率={summary['全市场等权']['win_rate']:>5.1f}%  中位收益={summary['全市场等权']['overall_median']:>+7.2f}%")

    output = {
        'meta': {
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'ma_period': MA_PERIOD,
            'threshold': THRESHOLD,
            'forward_days': 66,
            'forward_label': '3个月',
            'circ_mv_unit': '元',
        },
        'summary': summary,
        'details_by_mv': {label: results_by_mv[label] for label, _, _ in mv_bins},
        'details_all': results_all,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 写入: {OUTPUT_PATH}")
    print(f"⏱️ 耗时: {time.time() - t0:.1f}s")


if __name__ == '__main__':
    main()
