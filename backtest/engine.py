"""
回测引擎

支持策略级别的历史模拟，包含手续费、滑点和印花税。
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from strategy.base import Signal, SignalType
from risk.position_sizing import calculate_position_size, calculate_r_multiple
from config.settings import config


@dataclass
class Trade:
    """已完成的交易"""
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    shares: int = 0
    stop_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    r_multiple: float = 0.0
    reason_in: str = ""
    reason_out: str = ""

    def close(self, date, price, commission, stamp_tax, reason=""):
        self.exit_date = date
        self.exit_price = price
        self.reason_out = reason
        # 盈亏 = (卖出价 - 买入价) × 股数 - 手续费
        gross_pnl = (price - self.entry_price) * self.shares
        buy_cost = self.entry_price * self.shares * commission
        sell_cost = price * self.shares * (commission + stamp_tax)
        self.pnl = gross_pnl - buy_cost - sell_cost
        self.pnl_pct = (price - self.entry_price) / self.entry_price * 100
        if self.stop_price > 0:
            self.r_multiple = calculate_r_multiple(
                self.entry_price, price, self.stop_price
            )


@dataclass
class BacktestResult:
    """回测结果"""
    strategy_name: str
    symbol: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    initial_capital: float

    # 收益
    final_capital: float = 0.0
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0

    # 风险
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0

    # 交易统计
    total_trades: int = 0
    win_trades: int = 0
    lose_trades: int = 0
    win_rate_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0

    # R 倍数统计（Van Tharp）
    avg_r: float = 0.0
    sqn: float = 0.0  # System Quality Number

    # 详细数据
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'═' * 55}",
            f"  回测报告: {self.strategy_name} | {self.symbol}",
            f"{'═' * 55}",
            f"  时间: {self.start_date.date()} ~ {self.end_date.date()}",
            f"  资金: ¥{self.initial_capital:,.0f} → ¥{self.final_capital:,.0f}",
            f"{'─' * 55}",
            f"  总收益率:   {self.total_return_pct:+.2f}%",
            f"  年化收益:   {self.annual_return_pct:+.2f}%",
            f"  最大回撤:   {self.max_drawdown_pct:.2f}%",
            f"  夏普比率:   {self.sharpe_ratio:.2f}",
            f"{'─' * 55}",
            f"  交易次数:   {self.total_trades}",
            f"  胜率:       {self.win_rate_pct:.1f}%",
            f"  盈利因子:   {self.profit_factor:.2f}",
            f"  平均盈利:   {self.avg_win_pct:+.2f}%",
            f"  平均亏损:   {self.avg_loss_pct:.2f}%",
            f"{'─' * 55}",
            f"  平均 R 倍数: {self.avg_r:.2f}R",
            f"  SQN:        {self.sqn:.2f}  ({self._sqn_rating()})",
            f"{'═' * 55}",
        ]
        return "\n".join(lines)

    def _sqn_rating(self) -> str:
        """SQN 评级（Van Tharp 标准）"""
        if self.sqn >= 7.0:
            return "圣杯"
        elif self.sqn >= 5.1:
            return "极优"
        elif self.sqn >= 3.0:
            return "优秀"
        elif self.sqn >= 2.0:
            return "良好"
        elif self.sqn >= 1.7:
            return "中等"
        else:
            return "较差"


class BacktestEngine:
    """
    回测引擎

    接收策略产生的信号，模拟历史交易过程。
    """

    def __init__(
        self,
        initial_capital: float | None = None,
        commission_rate: float | None = None,
        slippage_rate: float | None = None,
        stamp_tax_rate: float | None = None,
    ):
        cfg = config.backtest
        self.initial_capital = initial_capital or cfg.initial_capital
        self.commission = commission_rate or cfg.commission_rate
        self.slippage = slippage_rate or cfg.slippage_rate
        self.stamp_tax = stamp_tax_rate or cfg.stamp_tax_rate

    def run(
        self,
        df: pd.DataFrame,
        signals: list[Signal],
        strategy_name: str = "",
        symbol: str = "",
    ) -> BacktestResult:
        """
        运行回测

        Args:
            df: 日线数据
            signals: 策略生成的信号列表
            strategy_name: 策略名称
            symbol: 标的代码

        Returns:
            BacktestResult
        """
        result = BacktestResult(
            strategy_name=strategy_name,
            symbol=symbol,
            start_date=df['日期'].min(),
            end_date=df['日期'].max(),
            initial_capital=self.initial_capital,
        )

        cash = self.initial_capital
        position = 0
        current_trade: Optional[Trade] = None

        # 按日期索引信号
        signal_map: dict[str, list[Signal]] = {}
        for s in signals:
            key = str(s.date.date())
            signal_map.setdefault(key, []).append(s)

        for _, row in df.iterrows():
            date = row['日期']
            price = row['收盘']
            date_key = str(date.date())

            day_signals = signal_map.get(date_key, [])

            for signal in day_signals:
                # 滑点调整
                exec_price = signal.price * (1 + self.slippage) if signal.signal_type == SignalType.BUY \
                    else signal.price * (1 - self.slippage)

                if signal.signal_type == SignalType.BUY and position == 0:
                    # 用风控计算仓位
                    stop = signal.stop_loss or (exec_price * 0.95)
                    ps = calculate_position_size(cash, exec_price, stop)

                    if ps.shares > 0:
                        cost = ps.shares * exec_price * (1 + self.commission)
                        if cost <= cash:
                            cash -= cost
                            position = ps.shares
                            current_trade = Trade(
                                symbol=symbol,
                                entry_date=date,
                                entry_price=exec_price,
                                shares=ps.shares,
                                stop_price=stop,
                                reason_in=signal.reason,
                            )

                elif signal.signal_type == SignalType.SELL and position > 0 and current_trade:
                    revenue = position * exec_price * (1 - self.commission - self.stamp_tax)
                    cash += revenue

                    current_trade.close(date, exec_price, self.commission, self.stamp_tax, signal.reason)
                    result.trades.append(current_trade)

                    position = 0
                    current_trade = None

            equity = cash + position * price
            result.equity_curve.append(equity)

        # 计算绩效
        result.final_capital = cash + position * df.iloc[-1]['收盘']
        result.total_return_pct = (result.final_capital - self.initial_capital) / self.initial_capital * 100

        days = (result.end_date - result.start_date).days
        if days > 0:
            result.annual_return_pct = ((result.final_capital / self.initial_capital) ** (365 / days) - 1) * 100

        # 最大回撤
        eq = pd.Series(result.equity_curve)
        peak = eq.cummax()
        dd = (eq - peak) / peak * 100
        result.max_drawdown_pct = dd.min()

        # 夏普比率
        returns = eq.pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            result.sharpe_ratio = (returns.mean() * 252 - 0.03) / (returns.std() * np.sqrt(252))

        # 交易统计
        result.total_trades = len(result.trades)
        if result.total_trades > 0:
            wins = [t for t in result.trades if t.pnl > 0]
            losses = [t for t in result.trades if t.pnl <= 0]
            result.win_trades = len(wins)
            result.lose_trades = len(losses)
            result.win_rate_pct = result.win_trades / result.total_trades * 100

            if wins:
                result.avg_win_pct = np.mean([t.pnl_pct for t in wins])
            if losses:
                result.avg_loss_pct = np.mean([t.pnl_pct for t in losses])

            total_win = sum(t.pnl for t in wins) if wins else 0
            total_loss = abs(sum(t.pnl for t in losses)) if losses else 0
            result.profit_factor = total_win / total_loss if total_loss > 0 else float('inf')

            # R 倍数统计
            r_values = [t.r_multiple for t in result.trades if t.r_multiple != 0]
            if r_values:
                result.avg_r = np.mean(r_values)
                # SQN = sqrt(N) × 平均R / R标准差
                r_std = np.std(r_values, ddof=1) if len(r_values) > 1 else 1.0
                if r_std > 0:
                    n = min(len(r_values), 100)  # Van Tharp 建议用 min(N, 100)
                    result.sqn = np.sqrt(n) * result.avg_r / r_std

        logger.info(f"回测完成: {strategy_name} | {symbol} | "
                    f"收益 {result.total_return_pct:+.2f}% | "
                    f"{result.total_trades} 笔交易")

        return result
