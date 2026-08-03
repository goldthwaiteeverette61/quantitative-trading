import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, '/opt/quantitative-trading')
from data.database import DatabaseManager
from data.universe import UniverseManager

def scan_bull_markets():
    db = DatabaseManager()
    um = UniverseManager()

    print("Fetching sample universe (CSI 1000 as broad market proxy)...")
    symbols = um.get_universe('csi1000') # 使用中证1000作为中小盘全市场的数学抽样

    conn = db._get_conn()
    symbol_str = "','".join(symbols)
    query = f"SELECT symbol as code, date, close FROM daily_kline WHERE symbol IN ('{symbol_str}') AND date >= '2014-01-01'"
    print("Loading historical data since 2014...")
    df = pd.read_sql_query(query, conn)
    conn.close()

    print("Calculating historical market breadth...")
    close_matrix = df.pivot(index='date', columns='code', values='close')
    ma120 = close_matrix.rolling(120).mean()

    # 计算每天所有股票站上120日均线的比例（市场赚钱效应广度）
    above_120 = (close_matrix > ma120).sum(axis=1)
    valid_stocks = close_matrix.notna().sum(axis=1)
    breadth = (above_120 / valid_stocks) * 100

    # 用 20 日均线平滑广度，防止单日剧震干扰
    smoothed_breadth = breadth.rolling(20).mean()

    print("\n=======================================================")
    print("🏆 A股历史上的真实【全面赚钱牛市】(中小盘主升浪)")
    print("判定标准: 连续长达数月，市场中有超过 65% 的股票位于均线之上")
    print("=======================================================\n")

    in_bull = False
    start_date = None

    bull_periods = []

    for date, val in smoothed_breadth.items():
        if pd.isna(val): continue
        if val > 65.0 and not in_bull:
            in_bull = True
            start_date = date
        elif val < 50.0 and in_bull: # 退潮至一半以下，判定牛市结束
            in_bull = False
            # 持续时间超过3个月才算作一场真牛市
            duration = (pd.to_datetime(date) - pd.to_datetime(start_date)).days
            if duration > 90:
                print(f"🔥 全面牛市段: {start_date} 🚀拉升至🚀 {date} (狂欢 {duration} 天)")
                bull_periods.append((start_date, date))

if __name__ == '__main__':
    scan_bull_markets()
