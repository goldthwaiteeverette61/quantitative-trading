"""
模拟交易模拟器 - V2 版本

支持组合策略的模拟运行，包含持仓管理、交易记录、绩效统计。
"""
import time
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
from rich.console import Console
from rich.table import Table
import pandas as pd

from data.fetcher import DataFetcher
from strategy.portfolio_base import PortfolioStrategy
from data.universe import get_universe_stocks

console = Console()


class Position:
    """持仓记录"""
    def __init__(self, code: str, name: str, shares: int, cost_price: float):
        self.code = code
        self.name = name
        self.shares = shares
        self.cost_price = cost_price
        self.current_price = cost_price
        self.buy_date = datetime.now()

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def profit(self) -> float:
        return (self.current_price - self.cost_price) * self.shares

    @property
    def profit_pct(self) -> float:
        return (self.current_price - self.cost_price) / self.cost_price * 100

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'name': self.name,
            'shares': self.shares,
            'cost_price': self.cost_price,
            'current_price': self.current_price,
            'market_value': self.market_value,
            'profit': self.profit,
            'profit_pct': self.profit_pct,
            'buy_date': self.buy_date.strftime('%Y-%m-%d'),
        }


class TradeRecord:
    """交易记录"""
    def __init__(
        self,
        code: str,
        action: str,
        shares: int,
        price: float,
        amount: float,
        reason: str = "",
    ):
        self.code = code
        self.action = action  # BUY / SELL
        self.shares = shares
        self.price = price
        self.amount = amount
        self.reason = reason
        self.time = datetime.now()

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'action': self.action,
            'shares': self.shares,
            'price': self.price,
            'amount': self.amount,
            'reason': self.reason,
            'time': self.time.strftime('%Y-%m-%d %H:%M:%S'),
        }


class PaperTradingSimulator:
    """
    模拟交易模拟器

    支持组合策略的模拟运行
    """

    def __init__(
        self,
        strategy: PortfolioStrategy,
        index_code: str = "csi500",
        initial_capital: float = 100000.0,
        top_n: int = 30,
    ):
        self.strategy = strategy
        self.index_code = index_code
        self.initial_capital = initial_capital
        self.top_n = top_n

        # 状态
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self.trade_log: list[TradeRecord] = []
        self.value_log: list[dict] = []

        # 工具
        self.fetcher = DataFetcher()

        logger.info(f"模拟交易模拟器初始化：{strategy.name}, 资金：{initial_capital:,.0f}")

    @property
    def total_market_value(self) -> float:
        """总市值"""
        return sum(p.market_value for p in self.positions.values())

    @property
    def total_value(self) -> float:
        """总资产"""
        return self.cash + self.total_market_value

    @property
    def total_profit(self) -> float:
        """总盈亏"""
        return self.total_value - self.initial_capital

    @property
    def total_profit_pct(self) -> float:
        """总收益率"""
        return self.total_profit / self.initial_capital * 100

    def get_target_weights(self, date: datetime) -> dict[str, float]:
        """
        获取目标权重

        调用策略的 get_target_weights 方法
        """
        # 获取股票池数据
        universe_df = self._get_universe_data(date)

        if universe_df is None or universe_df.empty:
            return {}

        # 调用策略
        try:
            weights = self.strategy.get_target_weights(date, universe_df)
            return weights
        except Exception as e:
            logger.error(f"策略计算失败：{e}")
            return {}

    def _get_universe_data(self, date: datetime) -> Optional[pd.DataFrame]:
        """获取股票池数据"""
        try:
            # 获取成分股
            stocks = get_universe_stocks(self.index_code)
            if not stocks:
                return None

            # 获取行情数据
            data = []
            for stock in stocks[:100]:  # 限制数量
                df = self.fetcher.get_stock_daily(stock, days=60)
                if df.empty:
                    continue

                latest = df.iloc[-1]
                data.append({
                    'code': stock,
                    'name': stock,
                    'close': latest['收盘'],
                    'market_cap': latest['收盘'] * 1e8,  # 简化估算
                    'pe': 15.0,  # 需要接入基本面数据
                    'pb': 1.5,
                    'roe': 10.0,
                    'momentum_20d': (latest['收盘'] / df.iloc[-20]['收盘'] - 1) * 100 if len(df) > 20 else 0,
                })

            if not data:
                return None

            df = pd.DataFrame(data)
            df.set_index('code', inplace=True)
            return df

        except Exception as e:
            logger.error(f"获取股票池数据失败：{e}")
            return None

    def rebalance(self, date: datetime) -> list[TradeRecord]:
        """
        调仓

        根据目标权重调整持仓
        """
        trades = []

        # 获取目标权重
        target_weights = self.get_target_weights(date)

        if not target_weights:
            return trades

        # 计算目标金额
        total_value = self.total_value
        target_amounts = {
            code: total_value * weight
            for code, weight in target_weights.items()
        }

        # 卖出不在目标中的股票
        for code in list(self.positions.keys()):
            if code not in target_weights:
                pos = self.positions[code]
                self._sell(pos, trades)

        # 买入/调整股票
        for code, target_amount in target_amounts.items():
            current_pos = self.positions.get(code)
            current_value = current_pos.market_value if current_pos else 0.0

            diff = target_amount - current_value

            if abs(diff) < 1000:  # 差异小于 1000 元不调整
                continue

            if diff > 0:
                # 买入
                self._buy(code, diff, trades)
            else:
                # 卖出
                if current_pos:
                    sell_shares = int(abs(diff) / current_pos.current_price / 100) * 100
                    if sell_shares >= 100:
                        self._sell(current_pos, trades, sell_shares)

        # 记录交易
        self.trade_log.extend(trades)

        return trades

    def _buy(self, code: str, amount: float, trades: list):
        """买入股票"""
        try:
            # 获取最新价格
            q = self.fetcher.get_realtime_quote(code)
            if not q:
                return

            price = q.get('最新价', 0)
            if price <= 0:
                return

            # 计算股数（100 的倍数）
            shares = int(amount / price / 100) * 100
            if shares < 100:
                return

            # 检查现金
            cost = shares * price * 1.0003  # 包含手续费
            if cost > self.cash:
                shares = int(self.cash * 0.95 / price / 100) * 100
                if shares < 100:
                    return

            # 更新持仓
            if code in self.positions:
                pos = self.positions[code]
                total_cost = pos.shares * pos.cost_price + shares * price
                pos.shares += shares
                pos.cost_price = total_cost / pos.shares
            else:
                pos = Position(code, q.get('名称', code), shares, price)
                self.positions[code] = pos

            # 更新现金
            self.cash -= shares * price * 1.0003

            # 记录交易
            trades.append(TradeRecord(
                code=code,
                action='BUY',
                shares=shares,
                price=price,
                amount=shares * price,
                reason='调仓买入',
            ))

            logger.info(f"买入 {code} {shares}股 @ ¥{price:.2f}")

        except Exception as e:
            logger.error(f"买入失败 {code}: {e}")

    def _sell(self, pos: Position, trades: list, shares: Optional[int] = None):
        """卖出股票"""
        try:
            # 获取最新价格
            q = self.fetcher.get_realtime_quote(pos.code)
            if not q:
                return

            price = q.get('最新价', pos.current_price)
            if price <= 0:
                return

            # 卖出股数
            sell_shares = shares or pos.shares
            if sell_shares <= 0:
                return

            # 更新持仓
            revenue = sell_shares * price * 0.9997  # 扣除手续费和印花税
            self.cash += revenue

            if sell_shares >= pos.shares:
                # 全部卖出
                del self.positions[pos.code]
            else:
                # 部分卖出
                pos.shares -= sell_shares

            # 记录交易
            trades.append(TradeRecord(
                code=pos.code,
                action='SELL',
                shares=sell_shares,
                price=price,
                amount=sell_shares * price,
                reason='调仓卖出',
            ))

            logger.info(f"卖出 {pos.code} {sell_shares}股 @ ¥{price:.2f}")

        except Exception as e:
            logger.error(f"卖出失败 {pos.code}: {e}")

    def update_prices(self):
        """更新持仓价格"""
        for code, pos in self.positions.items():
            try:
                q = self.fetcher.get_realtime_quote(code)
                if q:
                    pos.current_price = q.get('最新价', pos.current_price)
            except Exception as e:
                logger.error(f"更新价格失败 {code}: {e}")

    def record_value(self, date: datetime):
        """记录资产净值"""
        self.value_log.append({
            'date': date,
            'cash': self.cash,
            'market_value': self.total_market_value,
            'total_value': self.total_value,
            'profit': self.total_profit,
            'profit_pct': self.total_profit_pct,
        })

    def run_simulation(
        self,
        days: int = 60,
        rebalance_freq: str = 'monthly',
    ):
        """
        运行模拟交易

        Args:
            days: 模拟天数
            rebalance_freq: 调仓频率 (weekly/biweekly/monthly)
        """
        console.print(f"\n[bold cyan]═══ 模拟交易开始 ═══[/bold cyan]")
        console.print(f"策略：{self.strategy.name}")
        console.print(f"初始资金：¥{self.initial_capital:,.0f}")
        console.print(f"模拟天数：{days}天")
        console.print(f"调仓频率：{rebalance_freq}\n")

        # 模拟交易日循环
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        current_date = start_date

        rebalance_days = []
        if rebalance_freq == 'weekly':
            rebalance_days = list(range(0, days, 5))
        elif rebalance_freq == 'biweekly':
            rebalance_days = list(range(0, days, 10))
        else:  # monthly
            rebalance_days = list(range(0, days, 20))

        day_count = 0
        while current_date <= end_date:
            day_count += 1

            # 更新持仓价格
            self.update_prices()

            # 调仓
            if (day_count - 1) in rebalance_days:
                console.print(f"\n[bold]📅 第{day_count}天 - 调仓日[/bold]")
                trades = self.rebalance(current_date)
                if trades:
                    table = Table(show_header=True, header_style="bold")
                    table.add_column("时间", style="dim")
                    table.add_column("操作")
                    table.add_column("代码")
                    table.add_column("股数", justify="right")
                    table.add_column("价格", justify="right")
                    table.add_column("金额", justify="right")

                    for trade in trades:
                        table.add_row(
                            trade.time.strftime('%H:%M'),
                            f"[green]{trade.action}[/green]" if trade.action == 'BUY' else f"[red]{trade.action}[/red]",
                            trade.code,
                            str(trade.shares),
                            f"¥{trade.price:.2f}",
                            f"¥{trade.amount:,.0f}",
                        )
                    console.print(table)

            # 记录净值
            self.record_value(current_date)

            # 显示进度
            if day_count % 10 == 0:
                profit_color = "green" if self.total_profit >= 0 else "red"
                console.print(
                    f"[{current_date.strftime('%Y-%m-%d')}] "
                    f"总资产：¥{self.total_value:,.0f} "
                    f"盈亏：[bold {profit_color}]¥{self.total_profit:+,.0f} ({self.total_profit_pct:+.2f}%)[/bold {profit_color}]"
                )

            current_date += timedelta(days=1)

        # 最终报告
        self.print_report()

    def print_report(self):
        """打印模拟交易报告"""
        console.print(f"\n[bold cyan]═══ 模拟交易报告 ═══[/bold cyan]\n")

        # 总体表现
        table = Table(show_header=True, header_style="bold")
        table.add_column("指标", style="cyan")
        table.add_column("数值", justify="right")

        table.add_row("初始资金", f"¥{self.initial_capital:,.0f}")
        table.add_row("最终资产", f"¥{self.total_value:,.0f}")
        table.add_row("总盈亏", f"¥{self.total_profit:+,.0f}")
        table.add_row("总收益率", f"{self.total_profit_pct:+.2f}%")
        table.add_row("交易次数", f"{len(self.trade_log)}")

        console.print(table)

        # 持仓明细
        if self.positions:
            console.print(f"\n[bold]📊 当前持仓[/bold]")
            pos_table = Table(show_header=True, header_style="bold")
            pos_table.add_column("代码")
            pos_table.add_column("名称")
            pos_table.add_column("股数", justify="right")
            pos_table.add_column("成本", justify="right")
            pos_table.add_column("现价", justify="right")
            pos_table.add_column("盈亏", justify="right")
            pos_table.add_column("盈亏%", justify="right")

            for pos in self.positions.values():
                profit_color = "green" if pos.profit >= 0 else "red"
                pos_table.add_row(
                    pos.code,
                    pos.name,
                    str(pos.shares),
                    f"¥{pos.cost_price:.2f}",
                    f"¥{pos.current_price:.2f}",
                    f"[{profit_color}]¥{pos.profit:+,.0f}[/{profit_color}]",
                    f"[{profit_color}]{pos.profit_pct:+.2f}%[/{profit_color}]",
                )
            console.print(pos_table)

        # 交易记录
        if self.trade_log:
            console.print(f"\n[bold]📝 最近交易[/bold]")
            trade_table = Table(show_header=True, header_style="bold")
            trade_table.add_column("时间", style="dim")
            trade_table.add_column("操作")
            trade_table.add_column("代码")
            trade_table.add_column("股数", justify="right")
            trade_table.add_column("价格", justify="right")
            trade_table.add_column("金额", justify="right")
            trade_table.add_column("原因")

            for trade in self.trade_log[-10:]:
                action_color = "green" if trade.action == 'BUY' else "red"
                trade_table.add_row(
                    trade.time.strftime('%m-%d %H:%M'),
                    f"[{action_color}]{trade.action}[/{action_color}]",
                    trade.code,
                    str(trade.shares),
                    f"¥{trade.price:.2f}",
                    f"¥{trade.amount:,.0f}",
                    trade.reason,
                )
            console.print(trade_table)
