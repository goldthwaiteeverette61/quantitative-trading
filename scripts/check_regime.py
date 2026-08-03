import pandas as pd
import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, '/opt/quantitative-trading')
from data.database import DatabaseManager

def check_regime():
    db = DatabaseManager()
    conn = db._get_conn()

    # 找最近的有效交易日
    query_dates = "SELECT DISTINCT date FROM daily_kline ORDER BY date DESC LIMIT 220"
    dates_df = pd.read_sql_query(query_dates, conn)
    last_200_date = dates_df['date'].iloc[-1]

    # 拉取所有数据
    query = "SELECT symbol as code, date, close, amount FROM daily_kline WHERE date >= %s"
    df = pd.read_sql_query(query, conn, params=[last_200_date])
    conn.close()

    print(f"Latest Date: {df['date'].max()}")

    # 1. 广度 (Breadth)
    close_matrix = df.pivot(index='date', columns='code', values='close')
    ma120 = close_matrix.rolling(120).mean()
    ma200 = close_matrix.rolling(200).mean()

    latest_close = close_matrix.iloc[-1]
    latest_120 = ma120.iloc[-1]
    latest_200 = ma200.iloc[-1]

    valid_stocks = latest_close.dropna().count()
    above_120 = (latest_close > latest_120).sum()
    above_200 = (latest_close > latest_200).sum()

    pct_120 = above_120 / valid_stocks * 100
    pct_200 = above_200 / valid_stocks * 100

    # 2. 动能/流动性 (Volume)
    daily_amt = df.groupby('date')['amount'].sum()
    ma10_amt = daily_amt.rolling(10).mean().iloc[-1]
    ma60_amt = daily_amt.rolling(60).mean().iloc[-1]

    # 3. 波动率 (Volatility - 以全A等权中位数收益率拟合)
    returns = close_matrix.pct_change()
    index_rets = returns.median(axis=1) # 每日中位数收益
    vol_20d = index_rets.tail(20).std() * np.sqrt(250) * 100
    vol_100d = index_rets.iloc[-120:-20].std() * np.sqrt(250) * 100

    print("\n--- 市场三维指标输出 ---")
    print(f"1. 市场广度 (有效标的 {valid_stocks} 只):")
    print(f"   > 120日均线 (半年线): {pct_120:.1f}%")
    print(f"   > 200日均线 (年线)  : {pct_200:.1f}%")
    print(f"2. 市场量能 (去重全A成交额估值):")
    print(f"   近10日均量: {ma10_amt/1e8:.0f} 亿")
    print(f"   近60日均量: {ma60_amt/1e8:.0f} 亿")
    print(f"3. 波动率 (指数年化标准差):")
    print(f"   近期(20日): {vol_20d:.1f}%")
    print(f"   前期(100日): {vol_100d:.1f}%")

if __name__ == '__main__':
    check_regime()
