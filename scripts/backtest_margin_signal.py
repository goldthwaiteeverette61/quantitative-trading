"""
融资余额增速 — 逃顶信号胜率回测
============================================
信号定义:
  1. 获取上交所融资余额的历史数据
  2. 计算 20 日融资余额变化率
  3. 提取"过热逃顶"信号: 20日增速 > 历史 95 分位 → 逃顶信号
  4. 提取"底部枯竭"信号: 20日增速处于历史底部（融资持续萎缩）
  5. 分桶统计前瞻收益

输出: JSON 文件供 Web 面板渲染
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import akshare as ak
import pandas as pd
import numpy as np
import json
import time

OUTPUT_PATH = Path(__file__).parent.parent / 'web' / 'margin_report_data.json'


def main():
    print("=" * 70)
    print("💰 融资余额增速 — 逃顶信号胜率回测")
    print("   数据来源: 上交所融资融券汇总数据")
    print("=" * 70)

    t0 = time.time()

    # ====================================================================
    # Step 1: 获取融资余额数据
    # ====================================================================
    print("\n📥 [1/4] 从 AKShare 获取上交所融资余额数据...")
    df = ak.stock_margin_sse(start_date='20100101', end_date='20260401')
    df = df.rename(columns={
        '信用交易日期': 'date',
        '融资余额': 'margin_balance',
        '融资买入额': 'margin_buy',
        '融资融券余额': 'total_balance',
    })
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    df = df.sort_values('date')
    df = df.set_index('date')

    # 转换为数值
    df['margin_balance'] = pd.to_numeric(df['margin_balance'], errors='coerce')
    df['margin_buy'] = pd.to_numeric(df['margin_buy'], errors='coerce')

    print(f"   ✅ 共获取 {len(df)} 个交易日, 日期范围: {df.index.min().strftime('%Y-%m-%d')} ~ {df.index.max().strftime('%Y-%m-%d')}")
    print(f"   最新融资余额: ¥{df['margin_balance'].iloc[-1]:,.0f}")

    # ====================================================================
    # Step 2: 计算增速指标
    # ====================================================================
    print("\n📊 [2/4] 计算融资余额增速指标...")

    # 20 日变化率（约1个月）
    df['margin_chg_20d'] = df['margin_balance'].pct_change(periods=20) * 100
    # 5 日变化率（约1周，用于检测急速飙升）
    df['margin_chg_5d'] = df['margin_balance'].pct_change(periods=5) * 100
    # 60 日变化率（约3个月）
    df['margin_chg_60d'] = df['margin_balance'].pct_change(periods=60) * 100

    # 前瞻收益 — 这里用上证指数代理
    # 直接用融资余额数据的日期顺序来计算"等权"前瞻（融资数据本身就是市场热度的代理）
    # 用 margin_balance 自身变化来代理指数（两者高度相关）
    # 更好的做法：直接从 akshare 获取上证指数
    try:
        idx_df = ak.stock_zh_index_daily(symbol="sh000001")
        idx_df = idx_df.rename(columns={'date': 'date', 'close': 'index_close'})
        idx_df['date'] = pd.to_datetime(idx_df['date'])
        idx_df = idx_df.set_index('date')
        idx_df = idx_df[['index_close']].sort_index()
        df = df.join(idx_df, how='left')
        df['index_close'] = df['index_close'].ffill()
        print(f"   ✅ 上证指数数据已合并")
    except Exception as e:
        print(f"   ⚠️ 获取上证指数失败: {e}, 使用融资余额作为代理")
        df['index_close'] = df['margin_balance']

    # 前瞻收益
    for days, label in [(22, '1个月'), (66, '3个月'), (132, '6个月')]:
        df[f'fwd_{days}d'] = df['index_close'].pct_change(periods=days).shift(-days) * 100

    df = df.dropna(subset=['margin_chg_20d'])

    # ====================================================================
    # Step 3: 提取过热逃顶信号
    # ====================================================================
    print("\n🔍 [3/4] 提取过热逃顶信号...")

    # 信号: 20日融资增速 > 滚动90分位（动态阈值，适应不同时期）
    df['chg_90pct'] = df['margin_chg_20d'].rolling(500, min_periods=250).quantile(0.90)
    df['chg_95pct'] = df['margin_chg_20d'].rolling(500, min_periods=250).quantile(0.95)

    overheat_mask = (df['margin_chg_20d'] > df['chg_95pct']) & (df['margin_chg_20d'] > 3)

    # 去重: 每段连续信号只取第一天
    overheat_dates = df[overheat_mask].copy()
    if len(overheat_dates) > 0:
        dates_list = overheat_dates.index.tolist()
        filtered = [dates_list[0]]
        for d in dates_list[1:]:
            if (d - filtered[-1]).days > 30:
                filtered.append(d)
        overheat_dates = overheat_dates.loc[filtered]

    overheat_signals = []
    for date_val in overheat_dates.index:
        row = overheat_dates.loc[date_val]
        date_str = date_val.strftime('%Y-%m-%d')
        overheat_signals.append({
            'date': date_str,
            'margin_balance_bn': round(float(row['margin_balance']) / 1e8, 1),  # 亿元
            'chg_20d': round(float(row['margin_chg_20d']), 2),
            'chg_5d': round(float(row['margin_chg_5d']), 2) if pd.notna(row['margin_chg_5d']) else None,
            'threshold_95': round(float(row['chg_95pct']), 2) if pd.notna(row['chg_95pct']) else None,
            'fwd_1m': round(float(row['fwd_22d']), 2) if pd.notna(row['fwd_22d']) else None,
            'fwd_3m': round(float(row['fwd_66d']), 2) if pd.notna(row['fwd_66d']) else None,
            'fwd_6m': round(float(row['fwd_132d']), 2) if pd.notna(row['fwd_132d']) else None,
        })

    # 胜率计算 (过热后下跌 = 信号正确)
    valid_1m = [s for s in overheat_signals if s['fwd_1m'] is not None]
    valid_3m = [s for s in overheat_signals if s['fwd_3m'] is not None]
    valid_6m = [s for s in overheat_signals if s['fwd_6m'] is not None]

    correct_1m = len([s for s in valid_1m if s['fwd_1m'] < 0])
    correct_3m = len([s for s in valid_3m if s['fwd_3m'] < 0])
    correct_6m = len([s for s in valid_6m if s['fwd_6m'] < 0])

    overheat_stats = {
        'total_signals': len(overheat_signals),
        'escape_rate_1m': round(correct_1m / len(valid_1m) * 100, 1) if valid_1m else 0,
        'escape_rate_3m': round(correct_3m / len(valid_3m) * 100, 1) if valid_3m else 0,
        'escape_rate_6m': round(correct_6m / len(valid_6m) * 100, 1) if valid_6m else 0,
        'avg_1m': round(float(np.mean([s['fwd_1m'] for s in valid_1m])), 2) if valid_1m else 0,
        'avg_3m': round(float(np.mean([s['fwd_3m'] for s in valid_3m])), 2) if valid_3m else 0,
        'avg_6m': round(float(np.mean([s['fwd_6m'] for s in valid_6m])), 2) if valid_6m else 0,
        'median_1m': round(float(np.median([s['fwd_1m'] for s in valid_1m])), 2) if valid_1m else 0,
        'median_3m': round(float(np.median([s['fwd_3m'] for s in valid_3m])), 2) if valid_3m else 0,
        'median_6m': round(float(np.median([s['fwd_6m'] for s in valid_6m])), 2) if valid_6m else 0,
        'sample_1m': len(valid_1m),
        'sample_3m': len(valid_3m),
        'sample_6m': len(valid_6m),
    }

    print(f"\n📌 融资过热逃顶信号统计:")
    print(f"   总触发次数: {overheat_stats['total_signals']}")
    print(f"   1 个月逃顶成功率: {overheat_stats['escape_rate_1m']}% ({correct_1m}/{len(valid_1m)})")
    print(f"   3 个月逃顶成功率: {overheat_stats['escape_rate_3m']}% ({correct_3m}/{len(valid_3m)})")
    print(f"   6 个月逃顶成功率: {overheat_stats['escape_rate_6m']}% ({correct_6m}/{len(valid_6m)})")
    print(f"   3 个月中位数收益: {overheat_stats['median_3m']:+.2f}%")

    # ====================================================================
    # Step 4: 融资增速分桶统计
    # ====================================================================
    print("\n📊 [4/4] 融资增速分桶统计...")

    bins = [-100, -10, -5, -2, 0, 2, 5, 10, 20, 100]
    labels_bucket = ['<-10%', '-10~-5%', '-5~-2%', '-2~0%', '0~+2%', '+2~+5%', '+5~+10%', '+10~+20%', '>+20%']
    df['chg_bucket'] = pd.cut(df['margin_chg_20d'], bins=bins, labels=labels_bucket, right=True)

    bucket_stats = []
    for bucket in labels_bucket:
        subset = df[df['chg_bucket'] == bucket]
        if len(subset) < 10:
            continue
        bucket_stats.append({
            'bucket': bucket,
            'count': len(subset),
            'fwd_1m': round(float(subset['fwd_22d'].median()), 2),
            'fwd_3m': round(float(subset['fwd_66d'].median()), 2),
            'fwd_6m': round(float(subset['fwd_132d'].median()), 2),
        })

    print(f"\n   {'增速区间':<12} {'样本':<8} {'1月中位':<10} {'3月中位':<10} {'6月中位'}")
    print("-" * 55)
    for b in bucket_stats:
        emoji = "🟢" if b['fwd_3m'] > 3 else ("🟡" if b['fwd_3m'] > 0 else "🔴")
        print(f"   {emoji} {b['bucket']:<10} {b['count']:<8} {b['fwd_1m']:>+7.2f}%  {b['fwd_3m']:>+7.2f}%  {b['fwd_6m']:>+7.2f}%")

    # 输出 JSON
    output = {
        'meta': {
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'data_range': f"{df.index.min().strftime('%Y-%m-%d')} ~ {df.index.max().strftime('%Y-%m-%d')}",
            'data_source': '上交所融资融券汇总',
            'signal_definition': '20日融资增速 > 滚动95分位 且 > 3%',
        },
        'overheat': {
            'stats': overheat_stats,
            'signals': overheat_signals,
        },
        'bucket_stats': bucket_stats,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 数据已写入: {OUTPUT_PATH}")
    print(f"⏱️ 总耗时: {time.time() - t0:.1f}s")


if __name__ == '__main__':
    main()
