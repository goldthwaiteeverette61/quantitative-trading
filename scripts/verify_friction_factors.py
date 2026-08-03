#!/usr/bin/env python
"""
摩擦因子十分位分层回测验证 (Decile Portfolio Sorting)
"""

import sys, warnings
sys.path.insert(0, '/opt/quantitative-trading')
warnings.filterwarnings('ignore')

import time
import pandas as pd
import numpy as np
from loguru import logger
from data.database import DatabaseManager

def _calc_max_drawdown(nav_series):
    rolling_max = nav_series.cummax()
    drawdown = nav_series / rolling_max - 1.0
    return drawdown.min()

def run_decile_backtest(target_factor='turnover_vol', factor_name='换手波', reverse_sort=False):
    db = DatabaseManager()
    conn = db._get_conn()

    logger.info(f"========= 启动 {factor_name} ({target_factor}) 分层极压测试 =========")
    logger.info("1. 提取市场月末截面日期...")

    dates_df = pd.read_sql("SELECT DISTINCT date FROM factor_daily ORDER BY date", conn)
    dates_df['date'] = pd.to_datetime(dates_df['date'])
    dates_df['year_month'] = dates_df['date'].dt.to_period('M')
    # 提取每个月的最后一个交易日
    month_ends = dates_df.groupby('year_month')['date'].max().dt.strftime('%Y-%m-%d').tolist()

    logger.info(f" -> 提取到 {len(month_ends)} 个月末截面")
    if not month_ends:
        logger.error("无因子数据，请等待后台入库完成。")
        return

    logger.info("2. 从 MySQL 联表拉取价格与因子切片...")
    placeholders = ','.join(['%s'] * len(month_ends))
    query = f"""
    SELECT f.symbol, f.date, f.amihud_log, f.turnover_vol, f.idio_vol, k.close
    FROM factor_daily f
    JOIN daily_kline k ON f.symbol = k.symbol AND f.date = k.date
    WHERE f.date IN ({placeholders})
    """

    t0 = time.time()
    df = pd.read_sql(query, conn, params=month_ends)
    df['date'] = pd.to_datetime(df['date'])
    logger.info(f" -> 拉取完成，耗时 {time.time()-t0:.1f} 秒，共计 {len(df):,} 条月末截面数据")

    # 3. 计算前向一月收益率
    df = df.sort_values(['symbol', 'date'])
    df['forward_ret'] = df.groupby('symbol')['close'].shift(-1) / df['close'] - 1

    # 清理数据
    df = df.dropna(subset=[target_factor, 'forward_ret'])

    # 4. 横截面十分位打分 (Decile)
    logger.info("3. 执行截面横向十分位撕裂 (Decile Sort)...")

    def get_decile(series):
        try:
            # 如果 reverse_sort 为 True，数值越小越排在 G10（比如需要选大值的场景）
            # 默认 False：数值越小排在 G1（比如低波动），数值越大排在 G10（垃圾）
            sorted_s = series if not reverse_sort else -series
            return pd.qcut(sorted_s, 10, labels=[f'G{i}' for i in range(1, 11)], duplicates='drop')
        except:
            return pd.Series([None]*len(series), index=series.index)

    df['decile'] = df.groupby('date')[target_factor].transform(get_decile)
    df = df.dropna(subset=['decile'])

    # 5. 计算各组别的月度平均收益
    monthly_ret = df.groupby(['date', 'decile'])['forward_ret'].mean().unstack()
    monthly_ret = monthly_ret.fillna(0)

    # 6. 计算净值与性能指标
    navs = (1 + monthly_ret).cumprod()

    months_count = len(monthly_ret)
    years = months_count / 12.0

    print(f"\n--- {factor_name} ({target_factor}) 分层极压测试结果 (时长: {years:.2f}年) ---")
    print(f"| 分组 | 最终净值 | 年化收益 | 夏普比率 | 最大回撤 | 逻辑意义 |")
    print(f"|---|---|---|---|---|---|")

    # 无风险利率
    rf = 0.03

    summary_data = []

    for i in range(1, 11):
        g = f'G{i}'
        if g not in navs.columns:
            continue

        final_nav = navs[g].iloc[-1]

        # 年化收益 (复利)
        ann_ret = (final_nav ** (1 / years)) - 1

        # 年化波动 (月度波动 * sqrt(12))
        vol = monthly_ret[g].std() * np.sqrt(12)

        # 夏普 (用月均超额收益折算)
        sharpe = ((monthly_ret[g].mean() * 12) - rf) / vol if vol > 0 else 0

        # 最大回撤
        mdd = _calc_max_drawdown(navs[g])

        desc = ""
        if i == 1:
            desc = f"✨ 因子刻画的【最优质】前10%" if not reverse_sort else "✨ 因子刻画的【最大值】前10%"
        elif i == 10:
            desc = f"💣 游资情绪散户坑 (直接拉黑)" if not reverse_sort else "💣 因子值垫底的垃圾票"

        summary_data.append({
            'bucket': g,
            'ann_ret': ann_ret,
            'sharpe': sharpe,
            'mdd': mdd
        })

        print(f"| {g:<3} | {final_nav:>6.2f} | {ann_ret:>7.2%} | {sharpe:>6.2f} | {mdd:>7.2%} | {desc} |")

    print("\n[对冲与剥离检验]")
    outperform = summary_data[0]['ann_ret'] - summary_data[-1]['ann_ret']
    print(f"✅ G1 对冲 G10 的超额年化收益: {outperform:+.2%}")
    print(f"💡 策略建议：如果我们每个月直接将 {factor_name} 排名最差的10%股票加入绝对黑名单，")
    print(f"   至少能够规避掉 {summary_data[-1]['ann_ret']:.2%} 年化收益率的亏损深坑和爆仓风险！\n")

if __name__ == '__main__':
    run_decile_backtest(target_factor='turnover_vol', factor_name='换手波(Turnover Volatility)', reverse_sort=False)
    run_decile_backtest(target_factor='idio_vol', factor_name='特质波动率(Idiosyncratic Volatility)', reverse_sort=False)
    run_decile_backtest(target_factor='amihud_log', factor_name='Amihud非流动性指标', reverse_sort=True)
