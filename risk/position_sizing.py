"""
头寸管理 — Position Sizing

融合 Elder 2% 规则与 Van Tharp R 倍数理论。

核心原则：
- 单笔交易的最大亏损 = 总资金 × risk_pct（默认 2%）
- 仓位大小由"入场价到止损价的距离"决定，而非凭感觉
- R 倍数：将初始风险标准化为 1R，盈利用 R 倍数衡量
"""
from dataclasses import dataclass
from loguru import logger

from config.settings import config


@dataclass
class PositionSize:
    """仓位计算结果"""
    shares: int           # 建议股数（A 股按 100 整数倍）
    risk_amount: float    # 本笔风险金额
    position_value: float # 仓位市值
    risk_pct: float       # 实际风险占比


def calculate_position_size(
    capital: float,
    entry_price: float,
    stop_price: float,
    risk_pct: float | None = None,
) -> PositionSize:
    """
    基于风险计算仓位大小（Elder 2% 规则）

    仓位 = 可承受亏损金额 / 每股风险
    每股风险 = |入场价 - 止损价|

    Args:
        capital: 当前总资金
        entry_price: 入场价
        stop_price: 止损价
        risk_pct: 风险比例（默认取配置中的 0.02 即 2%）

    Returns:
        PositionSize 计算结果
    """
    if risk_pct is None:
        risk_pct = config.risk.max_single_risk_pct

    risk_amount = capital * risk_pct
    per_share_risk = abs(entry_price - stop_price)

    if per_share_risk <= 0:
        logger.warning("止损距离为零，无法计算仓位")
        return PositionSize(shares=0, risk_amount=0, position_value=0, risk_pct=0)

    raw_shares = risk_amount / per_share_risk
    # A 股按 100 股（1 手）取整
    shares = int(raw_shares / 100) * 100

    if shares <= 0:
        logger.warning(f"资金不足：需要至少 {100 * per_share_risk / risk_pct:.0f} 元")
        return PositionSize(shares=0, risk_amount=0, position_value=0, risk_pct=0)

    actual_risk = shares * per_share_risk
    position_value = shares * entry_price
    actual_risk_pct = actual_risk / capital

    logger.info(
        f"仓位计算: {shares}股 @ {entry_price:.2f}, "
        f"止损 {stop_price:.2f}, 风险 {actual_risk:.0f} ({actual_risk_pct:.2%})"
    )

    return PositionSize(
        shares=shares,
        risk_amount=actual_risk,
        position_value=position_value,
        risk_pct=actual_risk_pct,
    )


def calculate_r_multiple(
    entry_price: float,
    exit_price: float,
    stop_price: float,
) -> float:
    """
    计算 R 倍数（Van Tharp）

    R = 实际盈亏 / 初始风险
    - R > 0：盈利交易
    - R < 0：亏损交易
    - R = -1：恰好打止损

    Args:
        entry_price: 入场价
        exit_price: 出场价
        stop_price: 止损价

    Returns:
        R 倍数
    """
    initial_risk = abs(entry_price - stop_price)
    if initial_risk == 0:
        return 0.0

    pnl = exit_price - entry_price
    return pnl / initial_risk
