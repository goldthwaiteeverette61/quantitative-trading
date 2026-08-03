"""
组合策略评测器

基于组合回测结果计算综合评分（100 分制），并进行分级。
与 V1 evaluator.py 类似，但指标体系针对组合策略做了调整。
"""
from dataclasses import dataclass, field
from datetime import datetime

from backtest.portfolio_engine import PortfolioBacktestResult


@dataclass
class PortfolioDimensionScore:
    """单维度得分"""
    name: str
    raw_value: float
    score: float        # 0-100
    weight: float
    weighted: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "raw_value": round(self.raw_value, 4),
            "score": round(self.score, 1),
            "weight": self.weight,
            "weighted": round(self.weighted, 2),
        }


@dataclass
class PortfolioScore:
    """组合策略综合评分"""
    strategy_name: str
    index_name: str
    total_score: float = 0.0
    grade: str = ""
    grade_stars: int = 0
    dimensions: list[PortfolioDimensionScore] = field(default_factory=list)
    evaluated_at: str = ""

    # 原始数据摘要
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "index_name": self.index_name,
            "total_score": round(self.total_score, 1),
            "grade": self.grade,
            "grade_stars": self.grade_stars,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "evaluated_at": self.evaluated_at,
            "total_return_pct": round(self.total_return_pct, 2),
            "annual_return_pct": round(self.annual_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
        }


# ===== 评分维度 =====
# (名称, 权重, 满分阈值, 方向)
_DIMENSIONS = [
    ("年化收益",    0.25, 30.0,   "higher"),
    ("夏普比率",    0.25, 1.5,    "higher"),
    ("最大回撤",    0.20, -15.0,  "lower"),
    ("年化波动率",  0.15, 20.0,   "lower"),
    ("平均换手率",  0.15, 50.0,   "lower"),    # 换手率越低越好（减少交易成本）
]


def _score_dimension(value: float, threshold: float, direction: str) -> float:
    """计算单维度分数 (0-100)"""
    if direction == "higher":
        if threshold == 0:
            return 50.0
        raw_score = (value / threshold) * 100
    else:
        # lower: 值越小越好
        if threshold == 0:
            return 50.0
        if direction == "lower" and threshold < 0:
            # 回撤（负值），值越接近 0 越好
            if value > threshold:
                raw_score = max(100, (1 - (value - threshold) / abs(threshold)) * 100)
            else:
                raw_score = (1 - (value - threshold) / abs(threshold)) * 100
        else:
            # 正值 lower（如波动率、换手率）
            if value <= 0:
                return 100.0
            raw_score = (1 - value / threshold) * 100 + 100
            raw_score = max(0, min(100, raw_score))
            return raw_score

    return max(0.0, min(100.0, raw_score))


def evaluate_portfolio(result: PortfolioBacktestResult) -> PortfolioScore:
    """
    评测组合回测结果

    Args:
        result: 组合回测结果

    Returns:
        PortfolioScore
    """
    raw_values = {
        "年化收益": result.annual_return_pct,
        "夏普比率": result.sharpe_ratio,
        "最大回撤": result.max_drawdown_pct,
        "年化波动率": result.volatility_pct,
        "平均换手率": result.avg_turnover_pct,
    }

    dimensions = []
    total = 0.0

    for name, weight, threshold, direction in _DIMENSIONS:
        raw = raw_values.get(name, 0.0)
        score = _score_dimension(raw, threshold, direction)
        weighted = score * weight
        total += weighted
        dimensions.append(PortfolioDimensionScore(
            name=name,
            raw_value=raw,
            score=score,
            weight=weight,
            weighted=weighted,
        ))

    grade, stars = _classify(total)

    return PortfolioScore(
        strategy_name=result.strategy_name,
        index_name=result.index_name,
        total_score=total,
        grade=grade,
        grade_stars=stars,
        dimensions=dimensions,
        evaluated_at=datetime.now().isoformat(),
        total_return_pct=result.total_return_pct,
        annual_return_pct=result.annual_return_pct,
        max_drawdown_pct=result.max_drawdown_pct,
        sharpe_ratio=result.sharpe_ratio,
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
