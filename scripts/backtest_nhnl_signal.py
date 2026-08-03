"""
新高新低比 — 顶部背离逃顶信号胜率回测
============================================
信号定义:
  1. 计算每只股票的 250 日(52周) 滚动最高/最低价
  2. 每日统计全市场创新高数量 / 创新低数量
  3. 提取"顶部背离"信号: 指数创 60 日新高，但新高数量低于前次峰值 → 逃顶信号
  4. 提取"底部确认"信号: 冰点后新低数量持续衰减 → 底部确认

输出: JSON 文件供 Web 面板渲染
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

OUTPUT_PATH = Path(__file__).parent.parent / 'web' / 'nhnl_report_data.json'


def main():
    print("=" * 70)
    print("📊 新高新低比 — 顶部背离逃顶信号胜率回测")
    print("   计算全 A 股 52 周(250日) 新高/新低数量")
    print("=" * 70)

    t0 = time.time()

    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)
    print("\n📥 加载全 A 股历史 K 线 (high/low/close)...")
    query = """
        SELECT symbol, date, high, low, close
        FROM daily_kline
        WHERE date >= '2006-01-01'
        ORDER BY symbol, date
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = df['date'].astype(str)
    for col in ['high', 'low', 'close']:
        df[col] = df[col].astype(np.float32)
    df = df.sort_values(['symbol', 'date'])
    print(f"   ✅ 共加载 {len(df):,} 条记录, {df['symbol'].nunique()} 只股票")

    # ====================================================================
    # Step 1: 计算每只股票的 250 日滚动最高/最低价
    # ====================================================================
    print("\n📊 [1/4] 计算 250 日滚动新高新低...")
    df['rolling_high_250'] = df.groupby('symbol')['high'].transform(
        lambda x: x.rolling(250, min_periods=250).max()
    )
    df['rolling_low_250'] = df.groupby('symbol')['low'].transform(
        lambda x: x.rolling(250, min_periods=250).min()
    )

    # 前一天的滚动最高/最低（用于判断今天是否"首次"突破）
    df['prev_rolling_high'] = df.groupby('symbol')['rolling_high_250'].shift(1)
    df['prev_rolling_low'] = df.groupby('symbol')['rolling_low_250'].shift(1)

    # 今天创新高: 今天的 high >= 过去 250 天最高 且 大于前一日的250日高点
    df['is_new_high'] = ((df['high'] >= df['rolling_high_250']) &
                          (df['high'] > df['prev_rolling_high'])).astype(int)
    # 今天创新低: 今天的 low <= 过去 250 天最低 且 小于前一日的250日低点
    df['is_new_low'] = ((df['low'] <= df['rolling_low_250']) &
                         (df['low'] < df['prev_rolling_low'])).astype(int)

    df = df.dropna(subset=['rolling_high_250'])

    # ====================================================================
    # Step 2: 每日汇总新高新低数量
    # ====================================================================
    print("\n📈 [2/4] 每日汇总新高新低数量...")
    daily_nhnl = df.groupby('date').agg(
        new_highs=('is_new_high', 'sum'),
        new_lows=('is_new_low', 'sum'),
        total_stocks=('is_new_high', 'count')
    )
    daily_nhnl['nh_pct'] = daily_nhnl['new_highs'] / daily_nhnl['total_stocks'] * 100
    daily_nhnl['nl_pct'] = daily_nhnl['new_lows'] / daily_nhnl['total_stocks'] * 100
    daily_nhnl['nhnl_ratio'] = daily_nhnl['new_highs'] / (daily_nhnl['new_highs'] + daily_nhnl['new_lows'] + 1)

    # 全市场等权平均（用于前瞻收益）
    daily_avg = df.groupby('date')['close'].mean().sort_index()

    # 前瞻收益
    for days, label in [(22, '1个月'), (66, '3个月'), (132, '6个月')]:
        fwd = daily_avg.pct_change(periods=days).shift(-days) * 100
        daily_nhnl[f'fwd_{days}d'] = fwd

    # 指数代理（全市场等权平均）
    daily_nhnl['market_avg'] = daily_avg
    daily_nhnl['market_60d_high'] = daily_avg.rolling(60).max()

    daily_nhnl = daily_nhnl.dropna()

    # ====================================================================
    # Step 3: 提取顶部背离信号
    # ====================================================================
    print("\n🔍 [3/4] 提取顶部背离信号...")

    # 信号: 市场平均价处于 60日新高范围（±1%），但新高数量相比 20日峰值萎缩了 50% 以上
    daily_nhnl['nh_20d_peak'] = daily_nhnl['new_highs'].rolling(20).max()
    daily_nhnl['at_market_high'] = (daily_nhnl['market_avg'] >= daily_nhnl['market_60d_high'] * 0.99)
    daily_nhnl['nh_shrink_pct'] = (1 - daily_nhnl['new_highs'] / daily_nhnl['nh_20d_peak'].clip(lower=1)) * 100

    # 顶部背离触发: 市场在高位 + 新高数量大幅萎缩
    divergence_mask = (
        (daily_nhnl['at_market_high']) &
        (daily_nhnl['nh_shrink_pct'] >= 50) &
        (daily_nhnl['nh_20d_peak'] >= 50)  # 前期要有一定数量新高才有意义
    )

    # 去重: 每段连续信号只取第一天
    divergence_dates = daily_nhnl[divergence_mask].copy()
    if len(divergence_dates) > 0:
        dates_list = divergence_dates.index.tolist()
        filtered = [dates_list[0]]
        for d in dates_list[1:]:
            prev = pd.to_datetime(filtered[-1])
            cur = pd.to_datetime(d)
            if (cur - prev).days > 30:  # 30天内的视为同一次信号
                filtered.append(d)
        divergence_dates = divergence_dates.loc[filtered]

    divergence_signals = []
    for date_str in divergence_dates.index:
        row = divergence_dates.loc[date_str]
        divergence_signals.append({
            'date': date_str,
            'new_highs': int(row['new_highs']),
            'peak_20d': int(row['nh_20d_peak']),
            'shrink_pct': round(float(row['nh_shrink_pct']), 1),
            'new_lows': int(row['new_lows']),
            'fwd_1m': round(float(row['fwd_22d']), 2) if pd.notna(row['fwd_22d']) else None,
            'fwd_3m': round(float(row['fwd_66d']), 2) if pd.notna(row['fwd_66d']) else None,
            'fwd_6m': round(float(row['fwd_132d']), 2) if pd.notna(row['fwd_132d']) else None,
        })

    # 胜率 (信号触发后市场下跌 = 信号正确)
    valid_1m = [s for s in divergence_signals if s['fwd_1m'] is not None]
    valid_3m = [s for s in divergence_signals if s['fwd_3m'] is not None]
    valid_6m = [s for s in divergence_signals if s['fwd_6m'] is not None]

    # 顶部背离 → 信号正确 = 后续下跌 (< 0)
    correct_1m = len([s for s in valid_1m if s['fwd_1m'] < 0])
    correct_3m = len([s for s in valid_3m if s['fwd_3m'] < 0])
    correct_6m = len([s for s in valid_6m if s['fwd_6m'] < 0])

    divergence_stats = {
        'total_signals': len(divergence_signals),
        'escape_rate_1m': round(correct_1m / len(valid_1m) * 100, 1) if valid_1m else 0,
        'escape_rate_3m': round(correct_3m / len(valid_3m) * 100, 1) if valid_3m else 0,
        'escape_rate_6m': round(correct_6m / len(valid_6m) * 100, 1) if valid_6m else 0,
        'avg_1m': round(np.mean([s['fwd_1m'] for s in valid_1m]), 2) if valid_1m else 0,
        'avg_3m': round(np.mean([s['fwd_3m'] for s in valid_3m]), 2) if valid_3m else 0,
        'avg_6m': round(np.mean([s['fwd_6m'] for s in valid_6m]), 2) if valid_6m else 0,
        'median_1m': round(float(np.median([s['fwd_1m'] for s in valid_1m])), 2) if valid_1m else 0,
        'median_3m': round(float(np.median([s['fwd_3m'] for s in valid_3m])), 2) if valid_3m else 0,
        'median_6m': round(float(np.median([s['fwd_6m'] for s in valid_6m])), 2) if valid_6m else 0,
        'sample_1m': len(valid_1m),
        'sample_3m': len(valid_3m),
        'sample_6m': len(valid_6m),
    }

    print(f"\n📌 顶部背离信号统计:")
    print(f"   总触发次数: {divergence_stats['total_signals']}")
    print(f"   1 个月逃顶成功率: {divergence_stats['escape_rate_1m']}%")
    print(f"   3 个月逃顶成功率: {divergence_stats['escape_rate_3m']}%")
    print(f"   6 个月逃顶成功率: {divergence_stats['escape_rate_6m']}%")

    # ====================================================================
    # Step 4: 按新高新低比分桶统计（类似广度分桶）
    # ====================================================================
    print("\n📊 [4/4] 新高新低比分桶统计...")

    # 用新高占比分桶
    bins_nh = [0, 0.5, 1, 2, 3, 5, 8, 15, 100]
    labels_nh = ['0~0.5%', '0.5~1%', '1~2%', '2~3%', '3~5%', '5~8%', '8~15%', '>15%']
    daily_nhnl['nh_bucket'] = pd.cut(daily_nhnl['nh_pct'], bins=bins_nh, labels=labels_nh, right=True)

    bucket_stats = []
    for bucket in labels_nh:
        subset = daily_nhnl[daily_nhnl['nh_bucket'] == bucket]
        if len(subset) < 10:
            continue
        bucket_stats.append({
            'bucket': bucket,
            'count': len(subset),
            'fwd_1m': round(float(subset['fwd_22d'].median()), 2),
            'fwd_3m': round(float(subset['fwd_66d'].median()), 2),
            'fwd_6m': round(float(subset['fwd_132d'].median()), 2),
        })

    # 新低占比分桶统计
    bins_nl = [0, 0.5, 1, 2, 3, 5, 8, 15, 100]
    labels_nl = ['0~0.5%', '0.5~1%', '1~2%', '2~3%', '3~5%', '5~8%', '8~15%', '>15%']
    daily_nhnl['nl_bucket'] = pd.cut(daily_nhnl['nl_pct'], bins=bins_nl, labels=labels_nl, right=True)

    nl_bucket_stats = []
    for bucket in labels_nl:
        subset = daily_nhnl[daily_nhnl['nl_bucket'] == bucket]
        if len(subset) < 10:
            continue
        nl_bucket_stats.append({
            'bucket': bucket,
            'count': len(subset),
            'fwd_1m': round(float(subset['fwd_22d'].median()), 2),
            'fwd_3m': round(float(subset['fwd_66d'].median()), 2),
            'fwd_6m': round(float(subset['fwd_132d'].median()), 2),
        })

    print("\n   新高占比分桶:")
    print(f"   {'桶':<10} {'样本':<8} {'1月中位':<10} {'3月中位':<10} {'6月中位'}")
    for b in bucket_stats:
        print(f"   {b['bucket']:<10} {b['count']:<8} {b['fwd_1m']:>+7.2f}%  {b['fwd_3m']:>+7.2f}%  {b['fwd_6m']:>+7.2f}%")

    print("\n   新低占比分桶:")
    print(f"   {'桶':<10} {'样本':<8} {'1月中位':<10} {'3月中位':<10} {'6月中位'}")
    for b in nl_bucket_stats:
        print(f"   {b['bucket']:<10} {b['count']:<8} {b['fwd_1m']:>+7.2f}%  {b['fwd_3m']:>+7.2f}%  {b['fwd_6m']:>+7.2f}%")

    # 输出 JSON
    output = {
        'meta': {
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'data_range': f"{daily_nhnl.index.min()} ~ {daily_nhnl.index.max()}",
            'lookback_period': '250日(52周)',
        },
        'divergence': {
            'stats': divergence_stats,
            'signals': divergence_signals,
        },
        'nh_bucket_stats': bucket_stats,
        'nl_bucket_stats': nl_bucket_stats,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 数据已写入: {OUTPUT_PATH}")
    print(f"⏱️ 总耗时: {time.time() - t0:.1f}s")


if __name__ == '__main__':
    main()
