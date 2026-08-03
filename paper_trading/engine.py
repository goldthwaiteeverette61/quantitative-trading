"""
模拟交易引擎

接入实时行情，运行策略，模拟交易过程。
"""
import time
from datetime import datetime
from loguru import logger
from rich.console import Console
from rich.table import Table

from data.fetcher import DataFetcher
from strategy.base import BaseStrategy, SignalType
from risk.position_sizing import calculate_position_size
from risk.risk_manager import RiskManager, Position

console = Console()


class PaperTradingEngine:
    """
    模拟交易引擎

    定时获取行情，运行策略，模拟下单。
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        symbol: str,
        capital: float = 100000.0,
    ):
        self.strategy = strategy
        self.symbol = symbol
        self.capital = capital
        self.cash = capital
        self.fetcher = DataFetcher()
        self.risk_mgr = RiskManager()
        self.trade_log: list[dict] = []

    def run_once(self) -> list[dict]:
        """
        运行一次策略分析

        Returns:
            信号列表（字典格式）
        """
        # 获取多周期数据
        data = self.fetcher.get_multi_period_data(self.symbol)

        if any(df.empty for df in data.values()):
            logger.warning("数据获取不完整")
            return []

        # 运行策略
        signals = self.strategy.generate_signals(data)

        results = []
        for signal in signals:
            signal.symbol = self.symbol

            if signal.signal_type == SignalType.BUY:
                # 风控检查
                stop = signal.stop_loss or (signal.price * 0.95)
                ps = calculate_position_size(self.cash, signal.price, stop)

                if ps.shares > 0:
                    allowed, reason = self.risk_mgr.can_open_position(
                        self.capital, ps.risk_amount, ps.position_value
                    )
                    if allowed:
                        self.risk_mgr.add_position(Position(
                            symbol=self.symbol,
                            shares=ps.shares,
                            entry_price=signal.price,
                            stop_price=stop,
                        ))
                        entry = {
                            "时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "方向": "买入",
                            "代码": self.symbol,
                            "价格": signal.price,
                            "股数": ps.shares,
                            "原因": signal.reason,
                        }
                        self.trade_log.append(entry)
                        results.append(entry)
                    else:
                        logger.warning(f"风控拒绝: {reason}")

            elif signal.signal_type == SignalType.SELL:
                if any(p.symbol == self.symbol for p in self.risk_mgr.positions):
                    self.risk_mgr.remove_position(self.symbol)
                    entry = {
                        "时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "方向": "卖出",
                        "代码": self.symbol,
                        "价格": signal.price,
                        "原因": signal.reason,
                    }
                    self.trade_log.append(entry)
                    results.append(entry)

        return results

    def run_loop(self, interval: int = 60):
        """
        持续运行策略监控

        Args:
            interval: 检查间隔（秒）
        """
        console.print(f"\n[bold cyan]模拟交易启动[/bold cyan]")
        console.print(f"策略: {self.strategy.name}")
        console.print(f"标的: {self.symbol}")
        console.print(f"资金: ¥{self.capital:,.0f}")
        console.print(f"间隔: {interval}秒\n")

        try:
            while True:
                # 获取实时行情
                quote = self.fetcher.get_realtime_quote(self.symbol)
                if quote:
                    console.print(
                        f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] "
                        f"{quote.get('名称', self.symbol)} "
                        f"¥{quote.get('最新价', 0):.2f} "
                        f"({quote.get('涨跌幅', 0):+.2f}%)"
                    )

                    # 检查止损
                    triggered = self.risk_mgr.check_stop_losses(
                        {self.symbol: quote['最新价']}
                    )
                    if triggered:
                        console.print(f"[bold red]⚠ 止损触发: {triggered}[/bold red]")

                # 定期运行策略
                results = self.run_once()
                for r in results:
                    style = "green" if r['方向'] == '买入' else "red"
                    console.print(f"[bold {style}]>>> {r['方向']} {r['代码']} "
                                  f"@ {r['价格']:.2f} | {r['原因']}[/bold {style}]")

                time.sleep(interval)

        except KeyboardInterrupt:
            console.print("\n[yellow]模拟交易停止[/yellow]")
            self._print_summary()

    def _print_summary(self):
        """打印交易日志"""
        if not self.trade_log:
            console.print("无交易记录")
            return

        table = Table(title="交易日志")
        table.add_column("时间")
        table.add_column("方向")
        table.add_column("代码")
        table.add_column("价格", justify="right")
        table.add_column("原因")

        for log in self.trade_log:
            style = "green" if log['方向'] == '买入' else "red"
            table.add_row(
                log['时间'],
                f"[{style}]{log['方向']}[/{style}]",
                log['代码'],
                f"{log['价格']:.2f}",
                log.get('原因', ''),
            )

        console.print(table)
