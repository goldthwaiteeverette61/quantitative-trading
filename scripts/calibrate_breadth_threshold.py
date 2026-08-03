"""
市场广度阈值科学校准器
用历史数据验证：广度 = X% 时，未来收益如何？
找出"最佳建仓广度"和"最佳撤退广度"的物理临界值
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

def main():
    print("=" * 70)
    print("🔬 市场广度阈值科学校准器")
    print("   方法: 用全A历史数据反推最优广度建仓/撤退临界点")
    print("=" * 70)

    conn = pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

    # 1. 拉取所有股票的日线数据（只要 date, close）
    print("\n📥 [1/4] 正在从数据库加载历史日线...")
    query = """
        SELECT symbol, date, close
        FROM daily_kline
        WHERE date >= '2015-01-01'
        ORDER BY symbol, date
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    print(f"   ✅ 共加载 {len(df):,} 条记录, {df['symbol'].nunique()} 只股票")

    # 2. 计算每只股票的 120 日均线，判断是否站上
    print("\n📊 [2/4] 计算每只股票的 120 日均线站立状态...")
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values(['symbol', 'date'])
    df['ma120'] = df.groupby('symbol')['close'].transform(lambda x: x.rolling(120, min_periods=120).mean())
    df['above_ma120'] = (df['close'] > df['ma120']).astype(int)
    df = df.dropna(subset=['ma120'])

    # 3. 计算每日的市场广度
    print("\n📈 [3/4] 计算每日全市场广度...")
    daily_breadth = df.groupby('date').agg(
        total=('above_ma120', 'count'),
        above=('above_ma120', 'sum')
    )
    daily_breadth['breadth_pct'] = (daily_breadth['above'] / daily_breadth['total'] * 100)

    # 同时计算一个"市场收益代理"：用所有股票等权平均收益
    daily_avg_close = df.groupby('date')['close'].mean()
    daily_avg_close = daily_avg_close.sort_index()

    # 计算未来 N 天的市场等权回报
    print("   计算未来 22/66/132 交易日（约1/3/6个月）的前瞻回报...")
    for days, label in [(22, '1个月'), (66, '3个月'), (132, '6个月')]:
        future_return = daily_avg_close.pct_change(periods=days).shift(-days) * 100
        daily_breadth[f'fwd_{days}d'] = future_return

    daily_breadth = daily_breadth.dropna()

    # 4. 按广度分桶统计
    print("\n🎯 [4/4] 按广度分桶，统计各桶内的前瞻收益...\n")

    bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    labels = ['0-10%', '10-20%', '20-30%', '30-40%', '40-50%',
              '50-60%', '60-70%', '70-80%', '80-90%', '90-100%']
    daily_breadth['breadth_bucket'] = pd.cut(daily_breadth['breadth_pct'], bins=bins, labels=labels, right=True)

    print("=" * 85)
    print(f"{'广度区间':<12} {'样本天数':>8} {'未来1月中位数':>14} {'未来3月中位数':>14} {'未来6月中位数':>14}")
    print("-" * 85)

    results = []
    for bucket in labels:
        subset = daily_breadth[daily_breadth['breadth_bucket'] == bucket]
        if len(subset) < 10:
            continue
        m1 = subset['fwd_22d'].median()
        m3 = subset['fwd_66d'].median()
        m6 = subset['fwd_132d'].median()
        count = len(subset)

        # Color coding hint
        emoji = "🟢" if m3 > 3 else ("🟡" if m3 > 0 else "🔴")

        print(f"{emoji} {bucket:<10} {count:>8}天   {m1:>+10.2f}%   {m3:>+10.2f}%   {m6:>+10.2f}%")
        results.append({
            'bucket': bucket, 'count': count,
            'fwd_1m': m1, 'fwd_3m': m3, 'fwd_6m': m6
        })

    print("=" * 85)

    # 找最优建仓点和撤退点
    df_res = pd.DataFrame(results)
    if not df_res.empty:
        best_entry = df_res.loc[df_res['fwd_3m'].idxmax()]
        worst_zone = df_res.loc[df_res['fwd_3m'].idxmin()]

        print(f"\n📌 【科学结论】")
        print(f"   ✅ 最佳建仓广度区间: {best_entry['bucket']}  (未来3个月中位数回报: {best_entry['fwd_3m']:+.2f}%)")
        print(f"   ⛔ 最危险广度区间:   {worst_zone['bucket']}  (未来3个月中位数回报: {worst_zone['fwd_3m']:+.2f}%)")

        # 找"由负转正"的临界区间
        for i in range(len(df_res) - 1):
            if df_res.iloc[i]['fwd_3m'] < 0 and df_res.iloc[i+1]['fwd_3m'] >= 0:
                print(f"   🔑 多空分水岭:       {df_res.iloc[i+1]['bucket']}  (广度升至此区间，未来3个月首次转正)")
                break

        print(f"\n💡 以上数据基于 {daily_breadth['breadth_pct'].index.min().strftime('%Y-%m-%d')} 至 {daily_breadth['breadth_pct'].index.max().strftime('%Y-%m-%d')} 共 {len(daily_breadth)} 个交易日的真实统计")
        print("   均线周期: 120日(半年线), 前瞻收益: 等权全A股平均")

if __name__ == '__main__':
    main()
