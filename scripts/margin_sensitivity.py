"""
融资余额增速逃顶信号 — 参数鲁棒性测试
=======================================
测试不同参数组合:
  变化率窗口: 5日, 10日, 20日, 40日, 60日
  滚动分位窗口: 250日, 500日, 750日
  分位阈值: 90分位, 95分位
共 5 × 3 × 2 = 30 种参数组合
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import akshare as ak
import pandas as pd
import numpy as np
import json
import time

OUTPUT_PATH = Path(__file__).parent.parent / 'web' / 'margin_sensitivity_data.json'

CHG_WINDOWS = [5, 10, 20, 40, 60]
ROLLING_WINDOWS = [250, 500, 750]
QUANTILES = [0.90, 0.95]


def main():
    print("=" * 70)
    print("🔬 融资余额增速 — 参数鲁棒性测试")
    print(f"   共 {len(CHG_WINDOWS)} × {len(ROLLING_WINDOWS)} × {len(QUANTILES)} = {len(CHG_WINDOWS)*len(ROLLING_WINDOWS)*len(QUANTILES)} 种参数组合")
    print("=" * 70)
    t0 = time.time()

    # 获取数据
    df = ak.stock_margin_sse(start_date='20100101', end_date='20260401')
    df = df.rename(columns={'信用交易日期': 'date', '融资余额': 'margin_balance'})
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    df = df.sort_values('date').set_index('date')
    df['margin_balance'] = pd.to_numeric(df['margin_balance'], errors='coerce')

    # 上证指数
    try:
        idx = ak.stock_zh_index_daily(symbol="sh000001")
        idx['date'] = pd.to_datetime(idx['date'])
        idx = idx.set_index('date')[['close']].sort_index()
        df = df.join(idx, how='left')
        df['close'] = df['close'].ffill()
    except:
        df['close'] = df['margin_balance']

    # 前瞻收益
    for days in [22, 66, 132]:
        df[f'fwd_{days}d'] = df['close'].pct_change(periods=days).shift(-days) * 100

    print(f"\n数据: {len(df)} 个交易日, {df.index.min().strftime('%Y-%m-%d')} ~ {df.index.max().strftime('%Y-%m-%d')}")

    results = []

    for chg_w in CHG_WINDOWS:
        df[f'chg_{chg_w}d'] = df['margin_balance'].pct_change(periods=chg_w) * 100

        for roll_w in ROLLING_WINDOWS:
            for q in QUANTILES:
                q_col = df[f'chg_{chg_w}d'].rolling(roll_w, min_periods=min(roll_w, 250)).quantile(q)
                mask = (df[f'chg_{chg_w}d'] > q_col) & (df[f'chg_{chg_w}d'] > 2)

                # 去重 30 天
                signal_df = df[mask].dropna(subset=[f'chg_{chg_w}d'])
                if len(signal_df) == 0:
                    continue
                dates_list = signal_df.index.tolist()
                filtered = [dates_list[0]]
                for d in dates_list[1:]:
                    if (d - filtered[-1]).days > 30:
                        filtered.append(d)
                signal_df = signal_df.loc[filtered]

                # 计算胜率
                v1m = signal_df['fwd_22d'].dropna()
                v3m = signal_df['fwd_66d'].dropna()
                v6m = signal_df['fwd_132d'].dropna()

                row = {
                    'chg_window': chg_w,
                    'roll_window': roll_w,
                    'quantile': int(q * 100),
                    'triggers': len(signal_df),
                    'escape_1m': round(float((v1m < 0).sum() / len(v1m) * 100), 1) if len(v1m) > 0 else 0,
                    'escape_3m': round(float((v3m < 0).sum() / len(v3m) * 100), 1) if len(v3m) > 0 else 0,
                    'escape_6m': round(float((v6m < 0).sum() / len(v6m) * 100), 1) if len(v6m) > 0 else 0,
                    'median_3m': round(float(v3m.median()), 2) if len(v3m) > 0 else 0,
                    'sample_3m': len(v3m),
                }
                results.append(row)

                label = f"chg={chg_w}d roll={roll_w}d q={int(q*100)}"
                print(f"  {label:<30} triggers={row['triggers']:<4} 3m逃顶率={row['escape_3m']:>5.1f}% med3m={row['median_3m']:>+6.2f}%")

    # 找最佳和最差参数
    valid = [r for r in results if r['triggers'] >= 5]
    if valid:
        best = max(valid, key=lambda x: x['escape_3m'])
        worst = min(valid, key=lambda x: x['escape_3m'])
        avg_escape = np.mean([r['escape_3m'] for r in valid])
        std_escape = np.std([r['escape_3m'] for r in valid])

        print(f"\n{'=' * 70}")
        print(f"📊 鲁棒性总结 (共 {len(valid)} 组有效参数, 要求 ≥5 次触发):")
        print(f"   3个月逃顶率 平均: {avg_escape:.1f}% ± {std_escape:.1f}%")
        print(f"   最佳: chg={best['chg_window']}d roll={best['roll_window']}d q={best['quantile']} → {best['escape_3m']}% ({best['triggers']}次)")
        print(f"   最差: chg={worst['chg_window']}d roll={worst['roll_window']}d q={worst['quantile']} → {worst['escape_3m']}% ({worst['triggers']}次)")

    output = {
        'meta': {
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'chg_windows': CHG_WINDOWS,
            'roll_windows': ROLLING_WINDOWS,
            'quantiles': [int(q*100) for q in QUANTILES],
            'total_combos': len(results),
            'valid_combos': len(valid),
            'avg_escape_3m': round(avg_escape, 1) if valid else 0,
            'std_escape_3m': round(std_escape, 1) if valid else 0,
        },
        'results': results,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 写入: {OUTPUT_PATH}")
    print(f"⏱️ 耗时: {time.time() - t0:.1f}s")


if __name__ == '__main__':
    main()
