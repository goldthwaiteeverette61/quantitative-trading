"""
北漂策略规模极限探测器 (Scale Matrix)
测试 1, 3, 5 年 x 10万, 30万, 2000万 的滑点与本金反噬矩阵
"""
import sys
import time
import pandas as pd
from pathlib import Path

# 配置路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.universe import UniverseManager
from data.fetcher import DataFetcher
from backtest.portfolio_engine import PortfolioBacktestEngine
from strategy.beipiao_microcap import BeipiaoMicrocapV2, BeipiaoMicrocapV3

def run_matrix():
    print("="*60)
    print(" 🚀 正在启动策略时空矩阵压力测试...")
    print(" 维度: [1年, 3年, 5年] x [10万, 30万, 2000万本金]")
    print("="*60)

    strategies = [
        ("V2-财务过滤版", BeipiaoMicrocapV2(top_n=20, min_roe=0.15, rebalance_freq="weekly")),
        ("V3-完全体追高版", BeipiaoMicrocapV3(top_n=20, min_roe=0.15, max_bias_20=0.05, rebalance_freq="weekly")),
    ]
    years_list = [1, 3, 5]
    capital_list = [300000]
    index_code = "all_a"

    # 第一步：一次性顶格提取 5 年数据入内，用 Pandas 截取来规避重复 fetch
    print("\n📥 [1/3] 正在加载市场近 5 年 (约1825天) 地狱级全量数据底座...")
    t0 = time.time()
    um = UniverseManager()
    symbols = um.get_universe(index_code)

    fetcher = DataFetcher()
    max_days = 1825
    base_price_data = {}

    for i, sym in enumerate(symbols):
        df = fetcher.get_stock_data(sym, 'daily', max_days, use_cache=True)
        if not df.empty and len(df) > 20:
            base_price_data[sym] = df
        if (i+1) % 1000 == 0:
            print(f"   ➤ 已加载 {i+1} 只标的...")

    print(f"✅ 数据矩阵装载完成! 耗时 {time.time()-t0:.1f} 秒\n")

    # 第二步：跑马
    results = []
    print("🥊 [2/3] 开始交叉计算矩阵 (共 18 组)...")

    for strat_name, strat in strategies:
        for years in years_list:
            days = years * 365
            print(f"   ✂️ 正在切片 {years} 年历史回溯期...")
            # 切片数据
            sliced_data = {}
            for sym, df in base_price_data.items():
                sliced_df = df.tail(days)
                if not sliced_df.empty and len(sliced_df) > 20:
                    sliced_data[sym] = sliced_df

            for cap in capital_list:
                print(f"      ▶ 运行 {strat_name} | {years}年 | {cap/10000:.0f}万 ...", end=" ", flush=True)
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

                    results.append({
                        "模型": strat_name,
                        "期限": f"{years}年",
                        "启动资金": f"{cap/10000:.0f}万",
                        "年化收益": f"{res.annual_return_pct:+.2f}%",
                        "总收益": f"{res.total_return_pct:+.2f}%",
                        "最大回撤": f"{res.max_drawdown_pct:.2f}%",
                        "总摩擦损耗": f"¥{friction:,.0f}",
                        "损耗/本金比": f"{friction_ratio:.1f}%"
                    })
                    print(f"完成 ({(time.time()-t_run):.1f}秒)")
                except Exception as e:
                    print(f"❌ 失败: {e}")

    # 第三步：生成最终 Markdown
    print("\n" + "="*80)
    print("📊【北漂策略时空与体量极限界限报告】")
    print("="*80)
    df_res = pd.DataFrame(results)
    print(df_res.to_markdown(index=False))

if __name__ == '__main__':
    run_matrix()
