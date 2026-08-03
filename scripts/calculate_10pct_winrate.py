"""
冰点广度雷达 — 核心计算引擎
从当前时间往前推算，定位 60/120/250 日均线下市场广度首次跌破 10% 的触发日，
计算买入后持有 3 个月 / 6 个月的胜率和收益统计。
输出结构化 JSON 供 Web 面板渲染。
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

MA_PERIODS = [60, 120, 250]
BREADTH_THRESHOLD = 10.0  # 广度阈值
FWD_DAYS = [(66, '3个月'), (132, '6个月')]
OUTPUT_PATH = Path(__file__).parent.parent / 'web' / 'breadth_report_data.json'


def main():
    print("=" * 70)
    print("📉 冰点广度雷达 — 核心计算引擎")
    print("   信号定义: 广度首次从 ≥10% 跌破 <10% 的那一天 = 买入触发日")
    print("=" * 70)

    t0 = time.time()

    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME)

    # 拉取全量数据（从2006年至今）
    print("\n📥 正在加载全 A 股历史 K 线...")
    query = "SELECT symbol, date, close FROM daily_kline WHERE date >= '2006-01-01' ORDER BY symbol, date"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df['date'] = df['date'].astype(str)
    df['close'] = df['close'].astype(np.float32)
    df = df.sort_values(['symbol', 'date'])
    print(f"   ✅ 共加载 {len(df):,} 条记录, {df['symbol'].nunique()} 只股票")

    # 全市场等权平均收盘价（用于计算前瞻收益）
    daily_avg = df.groupby('date')['close'].mean().sort_index()

    all_reports = {}

    for ma_period in MA_PERIODS:
        label = {60: '季线(60日)', 120: '半年线(120日)', 250: '年线(250日)'}[ma_period]
        print(f"\n{'=' * 70}")
        print(f"📊 正在计算 {label} 均线的市场广度...")
        print(f"{'=' * 70}")

        # 计算每只股票的均线
        df[f'ma'] = df.groupby('symbol')['close'].transform(
            lambda x: x.rolling(ma_period, min_periods=ma_period).mean()
        )
        df['above'] = (df['close'] > df['ma']).astype(np.float32)
        valid = df.dropna(subset=['ma'])

        # 每日广度
        daily_breadth = valid.groupby('date').agg(
            total=('above', 'count'),
            above_count=('above', 'sum')
        )
        daily_breadth['breadth_pct'] = daily_breadth['above_count'] / daily_breadth['total'] * 100

        # 前瞻收益
        for fwd_d, _ in FWD_DAYS:
            fwd_ret = daily_avg.pct_change(periods=fwd_d).shift(-fwd_d) * 100
            daily_breadth[f'fwd_{fwd_d}d'] = fwd_ret

        # ============================
        # 提取"首次跌穿 10%"的触发日
        # ============================
        breadth_series = daily_breadth['breadth_pct']
        prev_above = breadth_series.shift(1) >= BREADTH_THRESHOLD
        curr_below = breadth_series < BREADTH_THRESHOLD
        trigger_mask = prev_above & curr_below

        trigger_dates = daily_breadth[trigger_mask].copy()
        trigger_dates = trigger_dates.dropna(subset=[f'fwd_{FWD_DAYS[0][0]}d'])  # 要求有前瞻数据

        # 构建冰点区间（从触发日开始，到广度重新回到 ≥10% 为止）
        ice_periods = []
        all_dates = breadth_series.index.tolist()

        for trig_date in trigger_dates.index:
            trig_idx = all_dates.index(trig_date)
            # 往后扫到 ≥10% 的那天
            end_date = trig_date
            duration = 1
            for j in range(trig_idx + 1, len(all_dates)):
                if breadth_series.iloc[j] >= BREADTH_THRESHOLD:
                    break
                end_date = all_dates[j]
                duration += 1

            min_breadth = breadth_series.loc[trig_date:end_date].min()

            # 前瞻收益
            fwd_3m = trigger_dates.loc[trig_date, f'fwd_{FWD_DAYS[0][0]}d']
            fwd_6m = trigger_dates.loc[trig_date, f'fwd_{FWD_DAYS[1][0]}d'] if f'fwd_{FWD_DAYS[1][0]}d' in trigger_dates.columns else None

            ice_periods.append({
                'trigger_date': trig_date,
                'end_date': end_date,
                'duration_days': duration,
                'trigger_breadth': round(float(breadth_series.loc[trig_date]), 2),
                'min_breadth': round(float(min_breadth), 2),
                'fwd_3m_return': round(float(fwd_3m), 2) if pd.notna(fwd_3m) else None,
                'fwd_6m_return': round(float(fwd_6m), 2) if pd.notna(fwd_6m) else None,
            })

        # 胜率统计
        valid_3m = [p for p in ice_periods if p['fwd_3m_return'] is not None]
        valid_6m = [p for p in ice_periods if p['fwd_6m_return'] is not None]

        win_3m = len([p for p in valid_3m if p['fwd_3m_return'] > 0])
        win_6m = len([p for p in valid_6m if p['fwd_6m_return'] > 0])

        avg_3m = np.mean([p['fwd_3m_return'] for p in valid_3m]) if valid_3m else 0
        avg_6m = np.mean([p['fwd_6m_return'] for p in valid_6m]) if valid_6m else 0
        median_3m = np.median([p['fwd_3m_return'] for p in valid_3m]) if valid_3m else 0
        median_6m = np.median([p['fwd_6m_return'] for p in valid_6m]) if valid_6m else 0

        stats = {
            'ma_period': ma_period,
            'ma_label': label,
            'total_triggers': len(ice_periods),
            'win_rate_3m': round(win_3m / len(valid_3m) * 100, 1) if valid_3m else 0,
            'win_rate_6m': round(win_6m / len(valid_6m) * 100, 1) if valid_6m else 0,
            'avg_return_3m': round(float(avg_3m), 2),
            'avg_return_6m': round(float(avg_6m), 2),
            'median_return_3m': round(float(median_3m), 2),
            'median_return_6m': round(float(median_6m), 2),
            'sample_3m': len(valid_3m),
            'sample_6m': len(valid_6m),
        }

        print(f"\n📌 {label} 统计结果:")
        print(f"   触发次数: {stats['total_triggers']}")
        print(f"   3 个月胜率: {stats['win_rate_3m']}% ({win_3m}/{len(valid_3m)})")
        print(f"   6 个月胜率: {stats['win_rate_6m']}% ({win_6m}/{len(valid_6m)})")
        print(f"   3 个月平均收益: {stats['avg_return_3m']:+.2f}%  中位数: {stats['median_return_3m']:+.2f}%")
        print(f"   6 个月平均收益: {stats['avg_return_6m']:+.2f}%  中位数: {stats['median_return_6m']:+.2f}%")

        all_reports[str(ma_period)] = {
            'stats': stats,
            'ice_periods': ice_periods,
        }

        # 清理临时列
        df.drop(columns=['ma', 'above'], inplace=True)

    # 元信息
    meta = {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'data_range': f"{daily_avg.index.min()} ~ {daily_avg.index.max()}",
        'threshold': BREADTH_THRESHOLD,
        'signal_definition': '广度首次从 ≥10% 跌破 <10% 的那一天',
    }

    output = {'meta': meta, 'reports': all_reports}

    # 写入 JSON
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 数据已写入: {OUTPUT_PATH}")
    print(f"⏱️ 总耗时: {time.time() - t0:.1f}s")


if __name__ == '__main__':
    main()
