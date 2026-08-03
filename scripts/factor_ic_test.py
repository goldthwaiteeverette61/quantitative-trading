"""
因子有效性极速检验脚本 (Vectorized Factor Test)
专门用于验证“在微小盘/全市场追涨杀跌(动量因子)”到底赚不赚钱。
使用纯 Pandas 矩阵运算，300万条数据 5 秒内算完。
"""

from data.database import DatabaseManager
import pandas as pd
import numpy as np
import time

def run_factor_test():
    print("🚀 启动因子极速检验 (2016 - 2026) ...")
    t0 = time.time()

    # 1. 极速加载全库数据
    db = DatabaseManager()
    conn = db._get_conn()
    query = "SELECT date, symbol, `close` FROM daily_kline"
    df = pd.read_sql_query(query, conn)
    conn.close()

    print(f"✅ 数据加载完成: {len(df)} 行记录，耗时 {time.time()-t0:.2f}s")

    # 2. 转换为宽表矩阵 (行=日期, 列=股票代码)
    t1 = time.time()
    df['date'] = pd.to_datetime(df['date'])
    close_matrix = df.pivot(index='date', columns='symbol', values='close')
    print(f"📊 矩阵构建完成: {close_matrix.shape[0]} 个交易日, {close_matrix.shape[1]} 只股票")

    # 3. 构造因子：20日动量 (过去20天的涨跌幅)
    # 取值越大，说明过去20天涨得越凶的“强势股”
    momentum_20 = close_matrix.pct_change(20)

    # 4. 构造未来收益：未来20天的涨跌幅 (用于验证买入上述股票后赚不赚钱)
    forward_return_20 = close_matrix.pct_change(20).shift(-20)

    # 5. 因子测试逻辑：每月月底（调仓日）根据动量将全市场分为 5 组
    # Q5: 动量最高（最近连扳、暴涨的股，即追高散户最爱的股）
    # ...
    # Q1: 动量最低（最近单边暴跌的股）

    # 每 20 个交易日采样一次（模拟月度调仓）
    rebalance_dates = close_matrix.index[::20]

    group_returns = { 'Q1_暴跌股': [], 'Q2': [], 'Q3': [], 'Q4': [], 'Q5_暴涨股': [] }

    for date in rebalance_dates[:-1]: # 去掉最后一天，因为没有未来收益
        if date not in momentum_20.index or date not in forward_return_20.index:
            continue

        # 取出这一天的动量因子和对应的未来20天真实验证收益
        factor_row = momentum_20.loc[date].dropna()
        fwd_ret_row = forward_return_20.loc[date].dropna()

        # 排除价格极低的仙股 (粗略的基本面/退市过滤: 只要今天价格 > 3元)
        valid_symbols = close_matrix.loc[date][close_matrix.loc[date] > 3].index

        # 取交集
        common_symbols = factor_row.index.intersection(fwd_ret_row.index).intersection(valid_symbols)
        if len(common_symbols) < 50: # 当天有效股票太少则跳过
            continue

        factors = factor_row[common_symbols]
        fwd_rets = fwd_ret_row[common_symbols]

        # 分为 5 组 (q=5)
        try:
            labels = ['Q1_暴跌股', 'Q2', 'Q3', 'Q4', 'Q5_暴涨股']
            groups = pd.qcut(factors, q=5, labels=labels, duplicates='drop')

            # 计算每组在这20天的平均收益
            for label in labels:
                if label in groups.values:
                    # 选出该组的股票，计算这些股票未来20天的等权平均收益
                    group_symbols = factors[groups == label].index
                    avg_ret = fwd_rets[group_symbols].mean()
                    group_returns[label].append(avg_ret)
                else:
                    group_returns[label].append(0)
        except Exception as e:
            pass

    # 6. 计算累积收益
    print(f"\n{'='*50}")
    print("📈 20日动量因子(追涨杀跌) 10年期 测试结果 (2016-2026)")
    print(f"{'='*50}")

    for label in ['Q1_暴跌股', 'Q2', 'Q3', 'Q4', 'Q5_暴涨股']:
        rets = pd.Series(group_returns[label]).fillna(0)
        # 将期间收益复利计算： (1+r1) * (1+r2) ... - 1
        cum_ret = (1 + rets).prod() - 1
        win_rate = (rets > 0).mean() * 100

        print(f"【{label}组】:")
        print(f"  └─ 累计总收益: {cum_ret*100:>+8.1f}%")
        print(f"  └─ 每月胜率:   {win_rate:>8.1f}%")
        print(f"  └─ 平均月回:   {rets.mean()*100:>+8.2f}%")
        print("-" * 30)

if __name__ == '__main__':
    run_factor_test()
