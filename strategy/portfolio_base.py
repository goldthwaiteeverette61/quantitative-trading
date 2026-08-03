"""
组合策略基类 — V2 Portfolio 架构核心

所有组合策略必须继承 PortfolioStrategy 并实现 get_target_weights()。
与 V1 的 BaseStrategy（单股择时）平行共存。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class RebalanceConfig:
    """调仓配置"""
    frequency: str = "monthly"       # monthly / biweekly / weekly
    max_holdings: int = 20           # 最大持仓数量
    min_weight: float = 0.01         # 最小权重（低于此值不买入）


class PortfolioStrategy(ABC):
    """
    组合策略抽象基类

    与 V1 BaseStrategy 的关键区别:
    - BaseStrategy: generate_signals(单股K线) → 时间序列买卖信号
    - PortfolioStrategy: get_target_weights(日期, 全市场截面) → 目标持仓权重

    所有组合策略必须实现:
    - name: 策略名称
    - get_target_weights(): 返回目标持仓权重字典
    """

    def __init__(self, rebalance_config: RebalanceConfig | None = None):
        self.rebalance_config = rebalance_config or RebalanceConfig()

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        ...

    @property
    def category(self) -> str:
        """策略类别"""
        return "组合"

    @property
    def description(self) -> str:
        """策略描述"""
        return ""

    @property
    def author(self) -> str:
        """策略作者"""
        return "未知"

    @abstractmethod
    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        """
        计算当期目标持仓权重

        Args:
            date: 调仓日期
            universe_df: 全市场截面数据 DataFrame，index 为股票代码，列包含:
                - name: 名称
                - close: 最新收盘价
                - market_cap: 总市值(亿)
                - pe: 动态市盈率
                - pb: 市净率
                - roe: ROE(%)
                - momentum_20d: 20日涨幅(%)
                - momentum_60d: 60日涨幅(%)
                - turnover: 换手率(%)
                - volume: 成交量

        Returns:
            权重字典，如 {"000001": 0.05, "600519": 0.05, ...}
            所有权重之和应 ≤ 1.0（剩余部分为现金）
        """
        ...

    def get_rebalance_dates(self, trading_dates: list[pd.Timestamp]) -> list[pd.Timestamp]:
        """
        根据调仓频率筛选出调仓日期

        Args:
            trading_dates: 所有交易日列表

        Returns:
            调仓日期列表
        """
        if not trading_dates:
            return []

        freq = self.rebalance_config.frequency
        dates = sorted(trading_dates)
        rebalance_dates = []

        if freq == "monthly":
            # 月末最后一个交易日
            current_month = dates[0].month
            for i, d in enumerate(dates):
                if i + 1 < len(dates) and dates[i + 1].month != d.month:
                    rebalance_dates.append(d)
                elif i == len(dates) - 1:
                    rebalance_dates.append(d)
        elif freq == "biweekly":
            # 每隔 10 个交易日
            for i in range(0, len(dates), 10):
                rebalance_dates.append(dates[i])
        elif freq == "weekly":
            # 每周最后一个交易日
            current_week = dates[0].isocalendar()[1]
            for i, d in enumerate(dates):
                week = d.isocalendar()[1]
                if i + 1 < len(dates) and dates[i + 1].isocalendar()[1] != week:
                    rebalance_dates.append(d)
                elif i == len(dates) - 1:
                    rebalance_dates.append(d)
        elif freq == "weekly_monday":
            # 每周第一个交易日（对齐聚宽 run_weekly(func, 1) = 周一开盘）
            current_week = dates[0].isocalendar()[1]
            week_first_seen = True
            for i, d in enumerate(dates):
                week = d.isocalendar()[1]
                if week != current_week:
                    current_week = week
                    week_first_seen = True
                if week_first_seen:
                    rebalance_dates.append(d)
                    week_first_seen = False
        elif freq == "daily":
            # 每个交易日都调仓（用于日频信号驱动型策略）
            rebalance_dates = list(dates)

        return rebalance_dates

    def meta_dict(self) -> dict:
        """返回策略元数据（供注册表和 API 使用）"""
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "author": self.author,
            "type": "portfolio",
            "rebalance_frequency": self.rebalance_config.frequency,
            "max_holdings": self.rebalance_config.max_holdings,
        }
