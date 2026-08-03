"""
绩效分析

提供策略绩效的深度分析和多策略对比。
"""
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from backtest.engine import BacktestResult

console = Console()


def print_backtest_report(result: BacktestResult):
    """打印回测报告"""
    console.print(result.summary())

    # 交易明细表
    if result.trades:
        table = Table(title="交易明细", show_lines=True)
        table.add_column("入场日期", style="cyan")
        table.add_column("出场日期", style="cyan")
        table.add_column("买入价", justify="right")
        table.add_column("卖出价", justify="right")
        table.add_column("股数", justify="right")
        table.add_column("盈亏", justify="right")
        table.add_column("收益率", justify="right")
        table.add_column("R倍数", justify="right")

        for t in result.trades:
            pnl_style = "green" if t.pnl > 0 else "red"
            exit_date = str(t.exit_date.date()) if t.exit_date else "-"
            exit_price = f"{t.exit_price:.2f}" if t.exit_price else "-"

            table.add_row(
                str(t.entry_date.date()),
                exit_date,
                f"{t.entry_price:.2f}",
                exit_price,
                str(t.shares),
                f"[{pnl_style}]{t.pnl:+,.0f}[/{pnl_style}]",
                f"[{pnl_style}]{t.pnl_pct:+.2f}%[/{pnl_style}]",
                f"{t.r_multiple:+.2f}R" if t.r_multiple != 0 else "-",
            )

        console.print(table)


def print_r_distribution(result: BacktestResult):
    """打印 R 倍数分布"""
    r_values = [t.r_multiple for t in result.trades if t.r_multiple != 0]
    if not r_values:
        return

    console.print("\n[bold]R 倍数分布:[/bold]")

    bins = {
        "< -2R": 0, "-2R~-1R": 0, "-1R~0R": 0,
        "0R~1R": 0, "1R~2R": 0, "2R~3R": 0, "> 3R": 0,
    }

    for r in r_values:
        if r < -2:
            bins["< -2R"] += 1
        elif r < -1:
            bins["-2R~-1R"] += 1
        elif r < 0:
            bins["-1R~0R"] += 1
        elif r < 1:
            bins["0R~1R"] += 1
        elif r < 2:
            bins["1R~2R"] += 1
        elif r < 3:
            bins["2R~3R"] += 1
        else:
            bins["> 3R"] += 1

    max_count = max(bins.values()) if bins.values() else 1
    for label, count in bins.items():
        bar_len = int(count / max_count * 30) if max_count > 0 else 0
        bar = "█" * bar_len
        style = "red" if "-" in label and label != "0R~1R" else "green"
        console.print(f"  {label:>8s} │ [{style}]{bar}[/{style}] {count}")


def compare_strategies(results: list[BacktestResult]):
    """多策略对比"""
    table = Table(title="策略对比", show_lines=True)
    table.add_column("策略", style="bold")
    table.add_column("总收益", justify="right")
    table.add_column("年化", justify="right")
    table.add_column("最大回撤", justify="right")
    table.add_column("夏普", justify="right")
    table.add_column("胜率", justify="right")
    table.add_column("盈利因子", justify="right")
    table.add_column("SQN", justify="right")
    table.add_column("交易数", justify="right")

    for r in results:
        ret_style = "green" if r.total_return_pct > 0 else "red"
        table.add_row(
            r.strategy_name,
            f"[{ret_style}]{r.total_return_pct:+.2f}%[/{ret_style}]",
            f"{r.annual_return_pct:+.2f}%",
            f"{r.max_drawdown_pct:.2f}%",
            f"{r.sharpe_ratio:.2f}",
            f"{r.win_rate_pct:.1f}%",
            f"{r.profit_factor:.2f}",
            f"{r.sqn:.2f}",
            str(r.total_trades),
        )

    console.print(table)
