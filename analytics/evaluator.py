"""
策略评测器

基于回测结果计算策略综合评分（100 分制），并进行分级。
"""
from dataclasses import dataclass, field
from datetime import datetime

from backtest.engine import BacktestResult


@dataclass
class DimensionScore:
    """单维度得分"""
    name: str           # 指标名称
    raw_value: float    # 原始值
    score: float        # 该维度得分（0-100）
    weight: float       # 权重
    weighted: float     # 加权得分

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "raw_value": round(self.raw_value, 4),
            "score": round(self.score, 1),
            "weight": self.weight,
            "weighted": round(self.weighted, 2),
        }


@dataclass
class StrategyScore:
    """策略综合评分"""
    strategy_name: str
    symbol: str
    total_score: float = 0.0
    grade: str = ""
    grade_stars: int = 0
    dimensions: list[DimensionScore] = field(default_factory=list)
    evaluated_at: str = ""

    # 原始回测数据摘要
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0
    total_trades: int = 0

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "total_score": round(self.total_score, 1),
            "grade": self.grade,
            "grade_stars": self.grade_stars,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "evaluated_at": self.evaluated_at,
            "total_return_pct": round(self.total_return_pct, 2),
            "annual_return_pct": round(self.annual_return_pct, 2),
            "total_trades": self.total_trades,
        }


# ===== 评分规则 =====

# 各维度配置：(名称, 权重, 满分阈值, 方向)
# 方向：'higher' = 越高越好，'lower' = 越低越好
_DIMENSIONS = [
    ("SQN",     0.25, 3.0,   "higher"),
    ("夏普比率",  0.20, 1.5,   "higher"),
    ("最大回撤",  0.20, -10.0, "lower"),   # 注意回撤是负值
    ("盈利因子",  0.15, 2.0,   "higher"),
    ("胜率",     0.10, 50.0,  "higher"),
    ("交易次数",  0.10, 20.0,  "higher"),
]


def _score_dimension(value: float, threshold: float, direction: str) -> float:
    """
    计算单维度的分数（0-100）

    满分阈值为 100 分，不设上限（超过满分阈值可得 100+）
    但最终 clamp 到 [0, 100]
    """
    if direction == "higher":
        if threshold == 0:
            return 50.0
        raw_score = (value / threshold) * 100
    else:  # lower（回撤，值越小越好）
        # 回撤是负值：0% 是最好（100 分），threshold 是可接受下限（100 分）
        # 例如 threshold = -10，value = -5（只回撤了 5%）→ 得分高
        if threshold == 0:
            return 50.0
        raw_score = (1 - (value - threshold) / abs(threshold)) * 100
        # 如果回撤比阈值更少（如 -3% vs -10%），得更高分
        if value > threshold:  # 回撤更小（更好）
            raw_score = max(raw_score, 100)

    return max(0.0, min(100.0, raw_score))


def evaluate(result: BacktestResult) -> StrategyScore:
    """
    评测一个回测结果，返回综合评分

    Args:
        result: 回测结果

    Returns:
        StrategyScore
    """
    # 提取原始值
    raw_values = {
        "SQN": result.sqn,
        "夏普比率": result.sharpe_ratio,
        "最大回撤": result.max_drawdown_pct,
        "盈利因子": result.profit_factor,
        "胜率": result.win_rate_pct,
        "交易次数": float(result.total_trades),
    }

    dimensions = []
    total = 0.0

    for name, weight, threshold, direction in _DIMENSIONS:
        raw = raw_values.get(name, 0.0)
        score = _score_dimension(raw, threshold, direction)
        weighted = score * weight
        total += weighted
        dimensions.append(DimensionScore(
            name=name,
            raw_value=raw,
            score=score,
            weight=weight,
            weighted=weighted,
        ))

    # 分级
    grade, stars = _classify(total)

    return StrategyScore(
        strategy_name=result.strategy_name,
        symbol=result.symbol,
        total_score=total,
        grade=grade,
        grade_stars=stars,
        dimensions=dimensions,
        evaluated_at=datetime.now().isoformat(),
        total_return_pct=result.total_return_pct,
        annual_return_pct=result.annual_return_pct,
        total_trades=result.total_trades,
    )


def _classify(score: float) -> tuple[str, int]:
    """根据总分分级"""
    if score >= 75:
        return "精选", 3
    elif score >= 55:
        return "合格", 2
    elif score >= 35:
        return "观察", 1
    else:
        return "淘汰", 0
