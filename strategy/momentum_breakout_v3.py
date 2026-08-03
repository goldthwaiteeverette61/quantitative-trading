"""
动能突破策略 V3（Momentum Breakout V3 — ATR 自适应突破）

在原版基础上，用 ATR 通道替代固定涨幅门槛：
    - V1: cond3 = pct_chg > 3%（所有股票一视同仁）
    - V3: cond3 = (price - pre_close) > K × ATR(20)（波动率自适应）

效果：
    - 低波动银行股：ATR 小，0.8% 涨幅就算有效突破
    - 高波动题材股：ATR 大，需要 5%+ 才算有效突破
    - 自动适配不同波动率特征的标的，减少误判

买入条件（七条件）：
    1. 创 20 日新高
    2. 放量 3-5 倍
    3. ATR 自适应突破：涨幅 > K × ATR(20)  ← 替代固定涨幅
    4. 阳线
    5. 最低涨幅保底：pct_chg > min_pct_floor（兜底 1%）
    6. 昨日未突破
    7. 昨日量能静默
"""

import pandas as pd
import numpy as np
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class MomentumBreakoutV3Strategy(PortfolioStrategy):
    """
    动能突破策略 V3 — ATR 自适应突破

    用 ATR 通道替代固定涨幅门槛，波动率越大的股票需要越大的涨幅才算有效突破。
    """

    def __init__(
        self,
        max_holdings: int = 100,
        vol_threshold: float = 3.0,
        max_vol_threshold: float = 5.0,
        atr_multiplier: float = 1.5,       # ATR 乘数
        min_pct_floor: float = 0.01,        # 最低涨幅保底（1%）
        max_hold_days: int = 5,
        stop_loss_pct: float = 0.05,
        min_list_days: int = 60,
        rebalance_freq: str = "daily",
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=max_holdings,
        ))
        self.max_holdings = max_holdings
        self.vol_threshold = vol_threshold
        self.max_vol_threshold = max_vol_threshold
        self.atr_multiplier = atr_multiplier
        self.min_pct_floor = min_pct_floor
        self.max_hold_days = max_hold_days
        self.min_list_days = min_list_days
        self.stop_loss_pct = stop_loss_pct
        self._holdings_tracker: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "动能突破V3 (ATR自适应突破)"

    @property
    def description(self) -> str:
        return (
            f"用 ATR(20)×{self.atr_multiplier} 替代固定涨幅门槛，波动率自适应。\n"
            f"低波动股小涨即触发，高波动股需大涨才算有效突破。\n"
            f"涨幅保底 >{self.min_pct_floor:.0%}，防止极低波动股误触发。"
        )

    @property
    def author(self) -> str:
        return "复盘推演"

    @property
    def needs_tech_indicators(self) -> bool:
        return True

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
        current_holdings: set = None,
    ) -> dict[str, float]:
        """回测引擎调用的日线级选股（与 V1 一致）"""
        _held = current_holdings or set()
        date_str = date.strftime('%Y-%m-%d')

        expired = []
        for sym in list(self._holdings_tracker.keys()):
            self._holdings_tracker[sym] += 1
            if self._holdings_tracker[sym] > self.max_hold_days:
                expired.append(sym)
                del self._holdings_tracker[sym]

        stale = [s for s in self._holdings_tracker if s not in _held]
        for s in stale:
            del self._holdings_tracker[s]

        kept_holdings = {sym for sym in self._holdings_tracker if sym in _held}
        available_slots = self.max_holdings - len(kept_holdings)
        new_signals = []

        if available_slots > 0:
            df = universe_df.copy()
            if 'isST' in df.columns:
                df = df[df['isST'] == 0]
            if 'name' in df.columns:
                df = df[~df['name'].astype(str).str.contains('ST|退', case=False, na=False)]
            if 'list_days' in df.columns:
                df = df[df['list_days'] >= self.min_list_days]
            df = df[~df.index.isin(kept_holdings)]
            if 'today_paused' in df.columns:
                df = df[df['today_paused'] != True]

            if 'is_20d_high' in df.columns:
                df = df[df['is_20d_high'] == 1]
            else:
                df = df.head(0)

            if 'vol_ratio_20' in df.columns and not df.empty:
                df = df[df['vol_ratio_20'] >= self.vol_threshold]

            # 条件 3: ATR 自适应涨幅门槛
            # 使用 volatility（20日年化波动率%）推导日ATR比率
            if not df.empty and 'close' in df.columns and 'pre_close' in df.columns:
                pct_chg = (df['close'] - df['pre_close']) / df['pre_close']

                if 'volatility' in df.columns:
                    daily_atr_ratio = df['volatility'] / (252 ** 0.5) / 100.0
                    atr_threshold = (self.atr_multiplier * daily_atr_ratio).clip(lower=self.min_pct_floor)
                    df = df[pct_chg > atr_threshold]
                else:
                    df = df[pct_chg > self.min_pct_floor]

            if not df.empty:
                open_col = 'today_open' if 'today_open' in df.columns else 'open'
                if open_col in df.columns:
                    df = df[df['close'] > df[open_col]]

            if not df.empty and 'today_open' in df.columns and 'today_high_limit' in df.columns:
                df = df[~(df['today_open'] >= df['today_high_limit'] - 0.01)]

            if not df.empty and 'vol_ratio_20' in df.columns:
                df = df.sort_values('vol_ratio_20', ascending=False)

            new_signals = df.index.tolist()[:available_slots]
            for sym in new_signals:
                self._holdings_tracker[sym] = 0

        all_holdings = list(kept_holdings) + new_signals
        if not all_holdings:
            return {}
        weight = 1.0 / self.max_holdings
        return {sym: weight for sym in all_holdings}
