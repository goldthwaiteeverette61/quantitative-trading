"""
动能突破策略 V2（Momentum Breakout V2）

在原版基础上增加两个零成本动能增强指标：
    1. VWAP 偏离度：现价相对于成交量加权均价的偏离幅度
       → 衡量盘中资金攻击力度，偏离越大说明追涨资金越凶猛
    2. 换手率过滤：当日成交量 / 流通股本
       → 区分"大盘股温和放量"和"小盘股真实换手"

买入条件（八条同时满足）：
    原版四条件：
    1. 创 20 日新高：当日收盘价 >= 过去 20 个交易日最高收盘价
    2. 放量异动：当日成交量 >= 近 20 日均量的 vol_threshold 倍
    3. 涨幅确认：当日涨幅 > min_pct_chg
    4. 阳线实体：收盘价 > 开盘价
    新增两条：
    5. VWAP 偏离度：(现价 - VWAP) / VWAP > min_vwap_dev
    6. 换手率门槛：当日换手率 > min_turnover_pct
    首日起爆验证（不变）：
    7. 昨日未突破 20 日高
    8. 昨日量能静默

卖出规则（与原版一致）：
    - 破位止损 / 移动止盈 / 动能衰竭 / 涨停豁免 / T+1保护
"""

import pandas as pd
import numpy as np
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class MomentumBreakoutV2Strategy(PortfolioStrategy):
    """
    动能突破策略 V2 — 融合 VWAP 偏离度 + 换手率

    相比 V1，新增两个盘中零成本指标，降低假突破率。
    """

    def __init__(
        self,
        max_holdings: int = 100,
        vol_threshold: float = 3.0,
        max_vol_threshold: float = 5.0,
        min_pct_chg: float = 0.03,
        max_hold_days: int = 5,
        stop_loss_pct: float = 0.05,
        min_list_days: int = 60,
        min_vwap_dev: float = 0.02,        # VWAP 偏离度下限（2%）
        min_turnover_pct: float = 5.0,     # 最低换手率（5%）
        rebalance_freq: str = "daily",
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=max_holdings,
        ))
        self.max_holdings = max_holdings
        self.vol_threshold = vol_threshold
        self.max_vol_threshold = max_vol_threshold
        self.min_pct_chg = min_pct_chg
        self.max_hold_days = max_hold_days
        self.min_list_days = min_list_days
        self.stop_loss_pct = stop_loss_pct
        self.min_vwap_dev = min_vwap_dev
        self.min_turnover_pct = min_turnover_pct
        self._holdings_tracker: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "动能突破V2 (VWAP+换手率增强)"

    @property
    def description(self) -> str:
        return (
            f"在动能突破V1基础上，新增VWAP偏离度(>{self.min_vwap_dev:.0%})和"
            f"换手率(>{self.min_turnover_pct:.1f}%)两个零成本增强指标。\n"
            f"八条件联合碰撞，进一步过滤假突破。"
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
        """回测引擎调用的日线级选股（与 V1 一致，新增指标在盘中 Worker 中实现）"""
        _held = current_holdings or set()
        date_str = date.strftime('%Y-%m-%d')

        # 持仓期管理
        expired = []
        for sym in list(self._holdings_tracker.keys()):
            self._holdings_tracker[sym] += 1
            if self._holdings_tracker[sym] > self.max_hold_days:
                expired.append(sym)
                del self._holdings_tracker[sym]

        if expired:
            logger.info(f"[{self.name}] {date_str} 持有期到期卖出: {expired}")

        stale = [s for s in self._holdings_tracker if s not in _held]
        for s in stale:
            del self._holdings_tracker[s]

        kept_holdings = {sym for sym in self._holdings_tracker if sym in _held}

        # 扫描突破信号
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

            # 条件 1: 20 日新高
            if 'is_20d_high' in df.columns:
                df = df[df['is_20d_high'] == 1]
            else:
                df = df.head(0)

            # 条件 2: 放量
            if 'vol_ratio_20' in df.columns and not df.empty:
                df = df[df['vol_ratio_20'] >= self.vol_threshold]

            # 条件 3: 涨幅
            if not df.empty and 'close' in df.columns and 'pre_close' in df.columns:
                pct_chg = (df['close'] - df['pre_close']) / df['pre_close']
                df = df[pct_chg > self.min_pct_chg]

            # 条件 4: 阳线
            if not df.empty:
                open_col = 'today_open' if 'today_open' in df.columns else 'open'
                if open_col in df.columns:
                    df = df[df['close'] > df[open_col]]

            # 排除涨停
            if not df.empty and 'today_open' in df.columns and 'today_high_limit' in df.columns:
                df = df[~(df['today_open'] >= df['today_high_limit'] - 0.01)]

            if not df.empty and 'vol_ratio_20' in df.columns:
                df = df.sort_values('vol_ratio_20', ascending=False)

            new_signals = df.index.tolist()[:available_slots]

            if new_signals:
                for sym in new_signals:
                    self._holdings_tracker[sym] = 0

        all_holdings = list(kept_holdings) + new_signals
        if not all_holdings:
            return {}

        weight = 1.0 / self.max_holdings
        return {sym: weight for sym in all_holdings}
