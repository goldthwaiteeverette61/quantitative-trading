import sys
import time
import argparse
import pandas as pd
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.database import DatabaseManager
from data.universe import UniverseManager
from data.fetcher import DataFetcher
from backtest.portfolio_engine import PortfolioBacktestEngine
from strategy.beipiao_microcap import BeipiaoMicrocapV2, BeipiaoMicrocapV3

db = DatabaseManager()

def update_task(task_id, status, progress, new_log=None, results_json=None):
    conn = db._get_conn()
    cursor = conn.cursor()
    try:
        # Get existing logs
        if db._db_type == 'mysql':
            cursor.execute("SELECT logs FROM matrix_tasks WHERE id=%s", (task_id,))
        else:
            cursor.execute("SELECT logs FROM matrix_tasks WHERE id=?", (task_id,))

        row = cursor.fetchone()
        logs = row[0] if row and row[0] else ""

        if new_log:
            print(new_log)
            logs += f"[{time.strftime('%H:%M:%S')}] {new_log}\n"

        now_ts = time.strftime('%Y-%m-%d %H:%M:%S')
        if results_json:
            sql = "UPDATE matrix_tasks SET status=%s, progress=%s, logs=%s, results_json=%s, updated_at=%s WHERE id=%s"
            if db._db_type != 'mysql':
                sql = sql.replace('%s', '?')
            cursor.execute(sql, (status, progress, logs, results_json, now_ts, task_id))
        else:
            sql = "UPDATE matrix_tasks SET status=%s, progress=%s, logs=%s, updated_at=%s WHERE id=%s"
            if db._db_type != 'mysql':
                sql = sql.replace('%s', '?')
            cursor.execute(sql, (status, progress, logs, now_ts, task_id))

        if db._db_type != 'mysql':
            conn.commit()
        else:
            conn.commit()
    except Exception as e:
        print(f"Update error: {e}")
    finally:
        cursor.close()
        conn.close()

def run_worker(task_id, years_list, capital_list, strat_types):
    update_task(task_id, 'running', 0, "🚀 极限压测实验室 Worker 已启动...")
    update_task(task_id, 'running', 1, f"任务参数: 期限{years_list}, 本金{capital_list}")

    strategies = []
    if "V2" in strat_types:
        strategies.append(("V2-财务过滤版", BeipiaoMicrocapV2(top_n=20, min_roe=0.15, rebalance_freq="weekly")))
    if "V3" in strat_types:
        strategies.append(("V3-完全体追高版", BeipiaoMicrocapV3(top_n=20, min_roe=0.15, max_bias_20=0.05, rebalance_freq="weekly")))

    if not strategies:
        strategies = [("V2-财务过滤版", BeipiaoMicrocapV2(top_n=20, min_roe=0.15, rebalance_freq="weekly"))]

    index_code = "all_a"
    max_years = max(years_list) if years_list else 1
    max_days = max_years * 365 + 10 # buffer

    update_task(task_id, 'running', 5, f"📥 正在加载市场近 {max_years} 年全量数据底座...")

    t0 = time.time()
    um = UniverseManager()
    symbols = um.get_universe(index_code)

    fetcher = DataFetcher()
    base_price_data = {}

    for i, sym in enumerate(symbols):
        df = fetcher.get_stock_data(sym, 'daily', max_days, use_cache=True)
        if not df.empty and len(df) > 20:
            base_price_data[sym] = df
        if (i+1) % 1000 == 0:
            update_task(task_id, 'running', 5 + int((i+1)/len(symbols)*15), f"   ➤ 已装载 {i+1} 只全息底层切片...")

    update_task(task_id, 'running', 20, f"✅ 数据装载完成! 耗时 {time.time()-t0:.1f} 秒. 准备开启高维矩阵推演...")

    results = []
    total_combinations = len(strategies) * len(years_list) * len(capital_list)
    current_combo = 0

    for strat_name, strat in strategies:
        for years in years_list:
            days = years * 365
            sliced_data = {}
            for sym, df in base_price_data.items():
                sliced_df = df.tail(days)
                if not sliced_df.empty and len(sliced_df) > 20:
                    sliced_data[sym] = sliced_df

            for cap in capital_list:
                current_combo += 1
                progress = 20 + int((current_combo - 1) / total_combinations * 75)
                update_task(task_id, 'running', progress, f"▶ 开始演算靶点: {strat_name} | {years}年 | {cap/10000:.0f}万本金 ...")

                t_run = time.time()
                try:
                    engine = PortfolioBacktestEngine(
                        initial_capital=cap,
                        commission_rate=0.0003,
                        slippage_rate=0.005,
                        stamp_tax_rate=0.0005
                    )
                    res = engine.run(strat, sliced_data, index_code)

                    friction = res.total_trade_cost
                    friction_ratio = (friction / cap) * 100

                    summary = {
                        "model": strat_name,
                        "years": f"{years}年",
                        "capital": f"{cap/10000:.0f}万",
                        "annual_return": f"{res.annual_return_pct:+.2f}%",
                        "total_return": f"{res.total_return_pct:+.2f}%",
                        "max_drawdown": f"{res.max_drawdown_pct:.2f}%",
                        "friction": f"¥{friction:,.0f}",
                        "friction_ratio": f"{friction_ratio:.1f}%"
                    }
                    results.append(summary)
                    prog_done = 20 + int(current_combo / total_combinations * 75)
                    update_task(task_id, 'running', prog_done, f"✔️ 靶点演算完成 (耗时 {(time.time()-t_run):.1f}秒) | 收益: {res.total_return_pct:+.2f}%")
                except Exception as e:
                    update_task(task_id, 'running', progress, f"❌ 演算失败: {e}")

    update_task(task_id, 'completed', 100, f"🎉 全部矩阵压测已顺利完成！", results_json=json.dumps(results))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--task_id', type=str, required=True)
    parser.add_argument('--years', type=str, default="1", help="comma separated years e.g. 1,3,5")
    parser.add_argument('--capital', type=str, default="300000", help="comma separated capital e.g. 300000,20000000")
    parser.add_argument('--strats', type=str, default="V2", help="V2,V3")
    args = parser.parse_args()

    years_list = [int(x) for x in args.years.split(',')]
    capital_list = [int(x) for x in args.capital.split(',')]
    strat_types = args.strats.split(',')

    try:
        run_worker(args.task_id, years_list, capital_list, strat_types)
    except Exception as e:
        update_task(args.task_id, 'failed', 0, f"💥 严重异常导致工作进程崩溃: {e}")
