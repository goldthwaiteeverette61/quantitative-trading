"""
账户级风控管理器

实现 Elder 6% 规则和综合风险检查。

核心规则：
- 所有未平仓交易的总风险不超过总资金的 6%
- 单只股票仓位不超过总资金的 20%
- 最大持仓数量限制
"""
from dataclasses import dataclass, field
from loguru import logger

from config.settings import config


@dataclass
class Position:
    """持仓信息"""
    symbol: str
    shares: int
    entry_price: float
    stop_price: float
    current_price: float = 0.0

    @property
    def market_value(self) -> float:
        """市值"""
        price = self.current_price if self.current_price > 0 else self.entry_price
        return self.shares * price

    @property
    def unrealized_pnl(self) -> float:
        """浮动盈亏"""
        if self.current_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) * self.shares

    @property
    def risk_amount(self) -> float:
        """该仓位的风险金额（到止损的距离 × 股数）"""
        return abs(self.entry_price - self.stop_price) * self.shares


class RiskManager:
    """
    账户风控管理器

    在每次交易前检查是否满足风控条件。
    """

    def __init__(self):
        self.cfg = config.risk
        self.positions: list[Position] = []

    def total_risk(self) -> float:
        """当前所有持仓的总风险金额"""
        return sum(p.risk_amount for p in self.positions)

    def can_open_position(
        self,
        capital: float,
        new_risk_amount: float,
        new_position_value: float,
    ) -> tuple[bool, str]:
        """
        检查是否可以开新仓

        Args:
            capital: 当前总资金
            new_risk_amount: 新仓位的风险金额
            new_position_value: 新仓位的市值

        Returns:
            (是否允许, 原因)
        """
        # 1. 检查 6% 规则
        current_total_risk = self.total_risk()
        projected_risk = current_total_risk + new_risk_amount
        max_total_risk = capital * self.cfg.max_total_risk_pct

        if projected_risk > max_total_risk:
            return False, (
                f"违反6%规则：当前总风险 {current_total_risk:.0f} + "
                f"新增 {new_risk_amount:.0f} = {projected_risk:.0f} > "
                f"上限 {max_total_risk:.0f}"
            )

        # 2. 检查单只仓位比例
        max_single = capital * self.cfg.max_single_position_pct
        if new_position_value > max_single:
            return False, (
                f"单只仓位超限：{new_position_value:.0f} > {max_single:.0f} "
                f"({self.cfg.max_single_position_pct:.0%})"
            )

        # 3. 检查持仓数量
        if len(self.positions) >= self.cfg.max_positions:
            return False, f"持仓数已达上限 {self.cfg.max_positions}"

        return True, "通过风控检查"

    def add_position(self, position: Position):
        """添加持仓"""
        self.positions.append(position)
        logger.info(f"新增持仓: {position.symbol} {position.shares}股 @ {position.entry_price:.2f}")

    def remove_position(self, symbol: str):
        """移除持仓"""
        self.positions = [p for p in self.positions if p.symbol != symbol]
        logger.info(f"移除持仓: {symbol}")

    def update_prices(self, prices: dict[str, float]):
        """更新持仓现价"""
        for p in self.positions:
            if p.symbol in prices:
                p.current_price = prices[p.symbol]

    def check_stop_losses(self, prices: dict[str, float]) -> list[str]:
        """
        检查止损触发

        Returns:
            需要止损的股票代码列表
        """
        triggered = []
        for p in self.positions:
            current = prices.get(p.symbol, 0)
            if current > 0 and current <= p.stop_price:
                triggered.append(p.symbol)
                logger.warning(f"止损触发: {p.symbol} 现价 {current:.2f} <= 止损 {p.stop_price:.2f}")
        return triggered

    def summary(self) -> dict:
        """风控摘要"""
        total_value = sum(p.market_value for p in self.positions)
        total_risk = self.total_risk()
        total_pnl = sum(p.unrealized_pnl for p in self.positions)

        return {
            "持仓数": len(self.positions),
            "总市值": total_value,
            "总风险": total_risk,
            "浮动盈亏": total_pnl,
            "持仓": [
                {
                    "代码": p.symbol,
                    "股数": p.shares,
                    "入场价": p.entry_price,
                    "止损价": p.stop_price,
                    "现价": p.current_price,
                    "浮盈": p.unrealized_pnl,
                }
                for p in self.positions
            ],
        }
