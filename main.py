#!/usr/bin/env python3
"""
综合量化交易系统

融合 Alexander Elder 三重滤网、海龟交易法等经典交易理论。

用法:
    python main.py analyze -s 000001                      # 三重滤网分析
    python main.py backtest -s 000001 --strategy triple    # 回测
    python main.py backtest -s 000001 --strategy turtle    # 海龟回测
    python main.py compare -s 000001                       # 多策略对比
    python main.py paper -s 000001 --strategy triple       # 模拟交易
    python main.py quote 000001                            # 实时行情
"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent))

import click
from loguru import logger
from rich.console import Console

# 配置日志
logger.remove()
logger.add(sys.stderr, level="WARNING")
logger.add("logs/trading_{time:YYYY-MM-DD}.log", rotation="1 day", level="DEBUG")

console = Console()


def _get_strategy(name: str):
    """根据名称获取策略实例"""
    if name in ('triple', 'triple-screen', '三重滤网'):
        from strategy.triple_screen import TripleScreenStrategy
        return TripleScreenStrategy()
    elif name in ('turtle', '海龟'):
        from strategy.turtle import TurtleStrategy
        return TurtleStrategy()
    else:
        console.print(f"[red]未知策略: {name}[/red]")
        console.print("可用策略: triple (三重滤网), turtle (海龟)")
        raise SystemExit(1)


@click.group()
def cli():
    """综合量化交易系统 — 融合经典交易理论"""
    pass


@cli.command()
@click.option('--symbol', '-s', default='000001', help='股票代码')
@click.option('--days', '-d', default=120, help='日线数据天数')
def analyze(symbol: str, days: int):
    """三重滤网策略分析"""
    from data.fetcher import DataFetcher
    from strategy.triple_screen import TripleScreenStrategy
    from indicators.moving_average import ema
    from indicators.macd import macd
    from indicators.rsi import rsi
    from indicators.force_index import force_index
    from indicators.atr import atr

    console.print(f"\n[bold cyan]═══ 三重滤网分析: {symbol} ═══[/bold cyan]\n")

    fetcher = DataFetcher()
    data = fetcher.get_multi_period_data(symbol, daily_days=days, weekly_days=52)

    if data['weekly'].empty or data['daily'].empty:
        console.print("[red]数据获取失败[/red]")
        return

    # === 第一重滤网：周线趋势 ===
    strategy = TripleScreenStrategy()
    weekly_trend = strategy.screen_one(data['weekly'])
    latest_trend = weekly_trend.iloc[-1]

    console.print("[bold]▎第一重滤网 — 周线趋势[/bold]")
    if latest_trend > 0:
        console.print(f"  趋势方向: [green]↗ 看多[/green] (强度 {latest_trend:.1f})")
    elif latest_trend < 0:
        console.print(f"  趋势方向: [red]↘ 看空[/red] (强度 {latest_trend:.1f})")
    else:
        console.print(f"  趋势方向: [yellow]→ 中性[/yellow]")

    # 周线 EMA
    w_ema = ema(data['weekly']['收盘'], 13)
    console.print(f"  13周EMA: {w_ema.iloc[-1]:.2f} (方向: {'↗' if w_ema.diff().iloc[-1] > 0 else '↘'})")

    # 周线 MACD
    w_macd = macd(data['weekly']['收盘'])
    console.print(f"  MACD柱: {w_macd.histogram.iloc[-1]:.4f} "
                  f"(斜率: {'↗ 膨胀' if w_macd.hist_slope.iloc[-1] > 0 else '↘ 收缩'})")

    # === 第二重滤网：日线震荡 ===
    console.print(f"\n[bold]▎第二重滤网 — 日线震荡指标[/bold]")
    d_close = data['daily']['收盘']
    d_volume = data['daily']['成交量']

    rsi_val = rsi(d_close)
    fi_val = force_index(d_close, d_volume, 2)
    atr_val = atr(data['daily']['最高'], data['daily']['最低'], d_close)

    rsi_latest = rsi_val.iloc[-1]
    fi_latest = fi_val.iloc[-1]
    atr_latest = atr_val.iloc[-1]

    rsi_status = "超卖" if rsi_latest < 30 else ("超买" if rsi_latest > 70 else "正常")
    rsi_color = "red" if rsi_latest < 30 else ("red" if rsi_latest > 70 else "green")
    console.print(f"  RSI(14): [{rsi_color}]{rsi_latest:.1f}[/{rsi_color}] ({rsi_status})")

    fi_color = "red" if fi_latest < 0 else "green"
    fi_status = "空头压力" if fi_latest < 0 else "多头力量"
    console.print(f"  Force Index(2): [{fi_color}]{fi_latest:,.0f}[/{fi_color}] ({fi_status})")

    console.print(f"  ATR(14): {atr_latest:.2f}")

    # === 第三重滤网：交易信号 ===
    console.print(f"\n[bold]▎第三重滤网 — 信号汇总[/bold]")
    signals = strategy.generate_signals(data)

    if signals:
        recent = signals[-5:]  # 最近 5 个信号
        for s in recent:
            style = "green" if s.signal_type.value == "BUY" else "red"
            console.print(f"  [{style}]{s}[/{style}]")
    else:
        console.print("  暂无交易信号")

    # 综合建议
    console.print(f"\n[bold]▎综合判断[/bold]")
    if latest_trend > 0 and (fi_latest < 0 or rsi_latest < 30):
        console.print("  [green bold]趋势向上 + 短期回调 → 关注买入机会[/green bold]")
        stop = d_close.iloc[-1] - 2 * atr_latest
        console.print(f"  建议止损位: {stop:.2f} (现价 - 2×ATR)")
    elif latest_trend < 0:
        console.print("  [red]趋势向下 → 观望或做空[/red]")
    else:
        console.print("  [yellow]趋势不明 → 观望等待[/yellow]")

    console.print()


@cli.command()
@click.option('--symbol', '-s', default='000001', help='股票代码')
@click.option('--strategy', 'strategy_name', default='triple', help='策略: triple / turtle')
@click.option('--days', '-d', default=365, help='回测天数')
@click.option('--capital', '-c', default=100000, help='初始资金')
def backtest(symbol: str, strategy_name: str, days: int, capital: float):
    """策略回测"""
    from data.fetcher import DataFetcher
    from backtest.engine import BacktestEngine
    from analytics.metrics import print_backtest_report, print_r_distribution

    strategy = _get_strategy(strategy_name)
    console.print(f"\n[bold cyan]═══ 回测: {strategy.name} | {symbol} | {days}天 ═══[/bold cyan]\n")

    fetcher = DataFetcher()
    data = fetcher.get_multi_period_data(symbol, daily_days=days, weekly_days=days // 5)

    if data['daily'].empty:
        console.print("[red]数据获取失败[/red]")
        return

    signals = strategy.generate_signals(data)
    console.print(f"策略产生 {len(signals)} 个信号\n")

    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(data['daily'], signals, strategy.name, symbol)

    print_backtest_report(result)
    print_r_distribution(result)


@cli.command()
@click.option('--symbol', '-s', default='000001', help='股票代码')
@click.option('--days', '-d', default=365, help='回测天数')
@click.option('--capital', '-c', default=100000, help='初始资金')
def compare(symbol: str, days: int, capital: float):
    """多策略对比回测"""
    from data.fetcher import DataFetcher
    from backtest.engine import BacktestEngine
    from analytics.metrics import compare_strategies
    from strategy.triple_screen import TripleScreenStrategy
    from strategy.turtle import TurtleStrategy

    console.print(f"\n[bold cyan]═══ 策略对比: {symbol} | {days}天 ═══[/bold cyan]\n")

    fetcher = DataFetcher()
    data = fetcher.get_multi_period_data(symbol, daily_days=days, weekly_days=days // 5)

    if data['daily'].empty:
        console.print("[red]数据获取失败[/red]")
        return

    strategies = [TripleScreenStrategy(), TurtleStrategy()]
    results = []

    for strategy in strategies:
        console.print(f"运行 {strategy.name}...", end=" ")
        signals = strategy.generate_signals(data)
        engine = BacktestEngine(initial_capital=capital)
        result = engine.run(data['daily'], signals, strategy.name, symbol)
        results.append(result)
        console.print(f"[green]{len(signals)} 个信号, {result.total_return_pct:+.2f}%[/green]")

    console.print()
    compare_strategies(results)


@cli.command()
@click.option('--symbol', '-s', default='000001', help='股票代码')
@click.option('--strategy', 'strategy_name', default='triple', help='策略: triple / turtle')
@click.option('--capital', '-c', default=100000, help='初始资金')
@click.option('--interval', '-i', default=60, help='检查间隔（秒）')
def paper(symbol: str, strategy_name: str, capital: float, interval: int):
    """模拟交易"""
    from paper_trading.engine import PaperTradingEngine

    strategy = _get_strategy(strategy_name)
    engine = PaperTradingEngine(strategy, symbol, capital)
    engine.run_loop(interval)


@cli.command()
@click.argument('symbol')
def quote(symbol: str):
    """获取实时行情"""
    from data.fetcher import DataFetcher

    fetcher = DataFetcher()
    q = fetcher.get_realtime_quote(symbol)

    if q:
        pct = q.get('涨跌幅', 0)
        color = "green" if pct >= 0 else "red"
        console.print(f"\n[bold]{q.get('名称', symbol)}[/bold] ({q.get('代码', symbol)})")
        console.print(f"最新价: [{color}]¥{q.get('最新价', 0):.2f}[/{color}] "
                      f"([{color}]{pct:+.2f}%[/{color}])")
        console.print(f"成交量: {q.get('成交量', 0):,.0f}")
    else:
        console.print(f"[red]获取 {symbol} 行情失败[/red]")


if __name__ == '__main__':
    cli()
