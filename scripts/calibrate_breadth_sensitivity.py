"""
广度阈值多均线敏感性检验
验证"冰点买入/狂热撤退"结论是否跨参数稳健
同时测试 60日(季线)、120日(半年线)、250日(年线)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pymysql
import pandas as pd
import numpy as np

DB_HOST = 'localhost'
DB_USER = 'root'
DB_PASS = 'root123456'
DB_NAME = 'quant_trading'

MA_PERIODS = [60, 120, 250]

def main():
    print("=" * 80)
    print("🔬 广度阈值多均线敏感性检验")
    print("   验证结论是否跨参数稳健（60日/120日/250日）")
    print("=" * 80)

    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)

    print("\n📥 加载历史日线数据... (从2006年起，预估2500万条，请耐心等待)")
    query = "SELECT symbol, date, close FROM daily_kline WHERE date >= '2006-01-01' ORDER BY symbol, date"
    df = pd.read_sql_query(query, conn)
    conn.close()

    # 内存优化1：不进行 to_datetime，用字符串原生排序 (AAAA-MM-DD是天然有序的)
    df['date'] = df['date'].astype(str)
    # 内存优化2：将 close 强制转换为 float32 节省一半内存
    df['close'] = df['close'].astype(np.float32)

    df = df.sort_values(['symbol', 'date'])
    print(f"   ✅ 加载并优化完毕！共 {len(df):,} 条记录, {df['symbol'].nunique()} 只股票")

    # 市场等权平均价格（用于计算前瞻收益）
    daily_avg = df.groupby('date')['close'].mean().sort_index()

    all_results = {}

    for ma_period in MA_PERIODS:
        print(f"\n{'='*80}")
        print(f"📊 均线周期: {ma_period}日 ({'季线' if ma_period==60 else '半年线' if ma_period==120 else '年线'})")
        print(f"{'='*80}")

        # 计算每只股票的 MA
        df[f'ma{ma_period}'] = df.groupby('symbol')['close'].transform(
            lambda x: x.rolling(ma_period, min_periods=ma_period).mean()
        )
        df[f'above_{ma_period}'] = (df['close'] > df[f'ma{ma_period}']).astype(int)

        valid = df.dropna(subset=[f'ma{ma_period}'])

        # 每日广度
        daily_breadth = valid.groupby('date').agg(
            total=(f'above_{ma_period}', 'count'),
            above=(f'above_{ma_period}', 'sum')
        )
        daily_breadth['breadth_pct'] = daily_breadth['above'] / daily_breadth['total'] * 100

        # 前瞻收益
        for days, label in [(22, '1月'), (66, '3月')]:
            fwd = daily_avg.pct_change(periods=days).shift(-days) * 100
            daily_breadth[f'fwd_{days}d'] = fwd

        daily_breadth = daily_breadth.dropna()

        # 分桶
        bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        labels_bucket = ['0-10%', '10-20%', '20-30%', '30-40%', '40-50%',
                         '50-60%', '60-70%', '70-80%', '80-90%', '90-100%']
        daily_breadth['bucket'] = pd.cut(daily_breadth['breadth_pct'], bins=bins, labels=labels_bucket, right=True)

        print(f"\n{'广度区间':<12} {'样本':>6} {'未来1月中位数':>14} {'未来3月中位数':>14}")
        print("-" * 55)

        results = []
        for bucket in labels_bucket:
            subset = daily_breadth[daily_breadth['bucket'] == bucket]
            if len(subset) < 5:
                continue
            m1 = subset['fwd_22d'].median()
            m3 = subset['fwd_66d'].median()
            count = len(subset)
            emoji = "🟢" if m3 > 3 else ("🟡" if m3 > 0 else "🔴")
            print(f"{emoji} {bucket:<10} {count:>6}  {m1:>+10.2f}%  {m3:>+10.2f}%")
            results.append({'bucket': bucket, 'count': count, 'fwd_1m': m1, 'fwd_3m': m3})

        all_results[ma_period] = results

        # 清理临时列
        df.drop(columns=[f'ma{ma_period}', f'above_{ma_period}'], inplace=True)

    # 汇总对比
    print(f"\n{'='*80}")
    print("📌 三条均线结论横向对比")
    print(f"{'='*80}")
    print(f"\n{'广度区间':<12}", end="")
    for ma in MA_PERIODS:
        print(f"  {ma}日均线(3月)", end="")
    print()
    print("-" * 65)

    for bucket_label in ['0-10%', '10-20%', '20-30%', '30-40%', '40-50%',
                         '50-60%', '60-70%', '70-80%', '90-100%']:
        print(f"  {bucket_label:<10}", end="")
        for ma in MA_PERIODS:
            found = [r for r in all_results.get(ma, []) if r['bucket'] == bucket_label]
            if found:
                v = found[0]['fwd_3m']
                print(f"    {v:>+7.2f}%   ", end="")
            else:
                print(f"      N/A      ", end="")
        print()

    print(f"\n{'='*80}")
    print("🔬 稳健性判断:")

    # 检查"冰点建仓"是否在所有均线中成立
    low_breadth_positive = 0
    high_breadth_negative = 0
    for ma in MA_PERIODS:
        res = all_results.get(ma, [])
        low = [r for r in res if r['bucket'] in ('0-10%', '10-20%')]
        high = [r for r in res if r['bucket'] in ('80-90%', '90-100%')]
        if low and all(r['fwd_3m'] > 0 for r in low):
            low_breadth_positive += 1
        if high and any(r['fwd_3m'] < 0 for r in high):
            high_breadth_negative += 1

    if low_breadth_positive == len(MA_PERIODS):
        print("   ✅ 「冰点建仓」规律在 60/120/250日均线中全部成立 → 稳健！可复现！")
    else:
        print(f"   ⚠️ 「冰点建仓」仅在 {low_breadth_positive}/{len(MA_PERIODS)} 条均线中成立 → 依赖参数，不够稳健")

    if high_breadth_negative == len(MA_PERIODS):
        print("   ✅ 「狂热撤退」规律在 60/120/250日均线中全部成立 → 稳健！可复现！")
    else:
        print(f"   ⚠️ 「狂热撤退」仅在 {high_breadth_negative}/{len(MA_PERIODS)} 条均线中成立 → 依赖参数，不够稳健")

if __name__ == '__main__':
    main()
