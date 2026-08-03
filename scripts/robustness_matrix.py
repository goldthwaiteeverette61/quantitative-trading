#!/usr/bin/env python3
"""
JK_搅屎棍策略 — 参数鲁棒性压测矩阵
=====================================
目标：用科学的对照实验消除偶然性，回答三个问题：
  1. 持股数量（10 vs 20 vs 30）对收益的影响有多大？
  2. 不同时间跨度（1年 vs 3年）结果是否一致？
  3. 不同股票池（中证500 vs 全A股）策略是否稳定？

测试矩阵：2 股票池 × 3 持股数 × 2 时间 = 12 组对照实验
"""

import requests
import time
import json
import sys
from datetime import datetime

API = "http://localhost:8001"

# ===== 测试矩阵定义 =====
# 先跑快的（中证500），再跑慢的（全A股）
# 先跑短的（1年），再跑长的（3年）
TESTS = []
for pool in ['csi500', 'all_a']:
    for top_n in [10, 20, 30]:
        for years_label, days in [('1年', 243), ('3年', 729)]:
            TESTS.append({
                'pool': pool,
                'pool_name': '中证500' if pool == 'csi500' else '全A股',
                'top_n': top_n,
                'years_label': years_label,
                'days': days,
                'capital': 300000,
            })

POOL_NAMES = {'csi500': '中证500', 'all_a': '全A股'}


def submit_task(t):
    """提交回测任务"""
    r = requests.post(f"{API}/api/portfolio/backtest/async", json={
        'strategy_key': 'jk_spoiler',
        'index_code': t['pool'],
        'days': t['days'],
        'initial_capital': t['capital'],
        'top_n': t['top_n'],
        'stamp_tax_rate': 0.0005,
        'commission_rate': 0.0003,
        'slippage_rate': 0.005,
    })
    data = r.json()
    return data.get('task_id')


def wait_for_result(task_id, timeout=1200):
    """轮询等待任务完成"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(5)
        try:
            resp = requests.get(f"{API}/api/portfolio/backtest/task/{task_id}")
            info = resp.json()
        except Exception as e:
            print(f"    轮询异常: {e}")
            continue

        status = info.get('status', 'unknown')
        progress = info.get('progress', '')
        elapsed = time.time() - t0

        # not_found 说明任务还在排队期，继续等
        if status == 'not_found':
            if int(elapsed) % 30 < 6:
                print(f"    [{elapsed:.0f}s] 排队中...", flush=True)
            continue

        # 每 30 秒打印一次进度
        if int(elapsed) % 30 < 6:
            print(f"    [{elapsed:.0f}s] {status} | {progress}", flush=True)

        if status == 'completed':
            return info.get('result', {}), elapsed
        elif status == 'failed':
            return None, elapsed

    return None, time.time() - t0


def main():
    print(f"{'=' * 72}")
    print(f"  JK_搅屎棍策略 — 参数鲁棒性压测矩阵")
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  共 {len(TESTS)} 组对照实验，串行执行避免资源争抢")
    print(f"{'=' * 72}")

    results = []
    total_start = time.time()

    for i, t in enumerate(TESTS):
        print(f"\n{'─' * 72}")
        print(f"  [{i + 1}/{len(TESTS)}] {t['pool_name']} | 持股 {t['top_n']} 只 | 回测 {t['years_label']}")
        print(f"{'─' * 72}")

        task_id = submit_task(t)
        if not task_id:
            print(f"    ❌ 任务发射失败，跳过")
            continue

        print(f"    🚀 任务已发射: {task_id[:8]}...")
        result, elapsed = wait_for_result(task_id)

        if result:
            row = {
                'pool': t['pool'],
                'pool_name': t['pool_name'],
                'top_n': t['top_n'],
                'years_label': t['years_label'],
                'days': t['days'],
                'return_pct': result.get('total_return_pct', 0),
                'annual_return_pct': result.get('annual_return_pct', 0),
                'max_drawdown_pct': result.get('max_drawdown_pct', 0),
                'sharpe': result.get('sharpe_ratio', 0),
                'volatility_pct': result.get('volatility_pct', 0),
                'total_rebalances': result.get('total_rebalances', 0),
                'elapsed': round(elapsed, 1),
            }
            results.append(row)
            ret = row['return_pct']
            dd = row['max_drawdown_pct']
            sp = row['sharpe']
            print(f"    ✅ 完成! 收益={ret:+.2f}% | 回撤={dd:.2f}% | 夏普={sp:.2f} | 耗时={elapsed:.0f}s")
        else:
            print(f"    ❌ 任务失败或超时 (耗时={elapsed:.0f}s)")

    # ===== 输出最终战报 =====
    total_elapsed = time.time() - total_start
    print(f"\n\n{'═' * 80}")
    print(f"  JK_搅屎棍策略 — 参数鲁棒性压测矩阵 · 最终战报")
    print(f"  完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  总耗时: {total_elapsed / 60:.1f} 分钟")
    print(f"{'═' * 80}")

    hdr = f"{'股票池':<10}{'持股':<6}{'回测':<6}{'总收益%':>10}{'年化%':>10}{'最大回撤%':>10}{'夏普':>8}{'波动率%':>10}{'调仓次':>8}{'耗时':>8}"
    print(hdr)
    print(f"{'─' * 80}")

    for r in results:
        line = (
            f"{r['pool_name']:<10}"
            f"{r['top_n']:<6}"
            f"{r['years_label']:<6}"
            f"{r['return_pct']:>+9.2f}%"
            f"{r['annual_return_pct']:>+9.2f}%"
            f"{r['max_drawdown_pct']:>9.2f}%"
            f"{r['sharpe']:>8.2f}"
            f"{r['volatility_pct']:>9.2f}%"
            f"{r['total_rebalances']:>8}"
            f"{r['elapsed']:>7.0f}s"
        )
        print(line)

    print(f"{'─' * 80}")

    # ===== 关键结论自动提取 =====
    if results:
        best = max(results, key=lambda x: x['sharpe'])
        worst = min(results, key=lambda x: x['sharpe'])
        print(f"\n  🏆 最佳夏普: {best['pool_name']} {best['top_n']}只 {best['years_label']} → 夏普={best['sharpe']:.2f} 收益={best['return_pct']:+.2f}%")
        print(f"  💀 最差夏普: {worst['pool_name']} {worst['top_n']}只 {worst['years_label']} → 夏普={worst['sharpe']:.2f} 收益={worst['return_pct']:+.2f}%")

        # 持股数敏感性
        for pool in ['csi500', 'all_a']:
            pool_results = [r for r in results if r['pool'] == pool]
            if len(pool_results) >= 2:
                sharpes = [r['sharpe'] for r in pool_results]
                spread = max(sharpes) - min(sharpes)
                pname = POOL_NAMES[pool]
                if spread > 1.0:
                    print(f"  ⚠️ {pname}: 夏普波动幅度 {spread:.2f}，策略参数敏感性 【高】")
                elif spread > 0.5:
                    print(f"  ⚡ {pname}: 夏普波动幅度 {spread:.2f}，策略参数敏感性 【中】")
                else:
                    print(f"  ✅ {pname}: 夏普波动幅度 {spread:.2f}，策略参数敏感性 【低】，信号较稳定")

    # 保存结果
    output_path = '/opt/quantitative-trading/robustness_matrix.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  📁 原始数据已保存: {output_path}")


if __name__ == '__main__':
    main()
