"""
量能冲击观察策略

日线回测只做粗近似；模拟实盘使用 paper_trading.volume_shock_observer 的盘中快照 worker。
"""

import pandas as pd

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class VolumeShockObserverStrategy(PortfolioStrategy):
    """公开行情量能冲击后的短线延续性观察策略。"""

    def __init__(
        self,
        max_holdings: int = 5,
        vol_threshold: float = 3.0,
        max_vol_threshold: float = 8.0,
        min_pct_chg: float = 0.01,
        max_pct_chg: float = 0.06,
        min_amount: float = 30_000_000.0,
        max_hold_days: int = 3,
        rebalance_freq: str = "daily",
    ):
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=max_holdings))
        self.max_holdings = max_holdings
        self.vol_threshold = vol_threshold
        self.max_vol_threshold = max_vol_threshold
        self.min_pct_chg = min_pct_chg
        self.max_pct_chg = max_pct_chg
        self.min_amount = min_amount
        self.max_hold_days = max_hold_days
        self._holdings_tracker: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "量能冲击观察"

    @property
    def category(self) -> str:
        return "动量"

    @property
    def description(self) -> str:
        return (
            "模拟实盘观察策略：公开行情中出现量能冲击、价格站上均价且涨幅未过热时建仓，"
            "验证后续1-3日是否有延续性。默认不把它视为成熟 alpha。"
        )

    @property
    def author(self) -> str:
        return "实盘观察"

    @property
    def needs_tech_indicators(self) -> bool:
        return True

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
        current_holdings: set = None,
    ) -> dict[str, float]:
        held = current_holdings or set()

        expired = []
        for sym in list(self._holdings_tracker.keys()):
            self._holdings_tracker[sym] += 1
            if self._holdings_tracker[sym] > self.max_hold_days:
                expired.append(sym)
                del self._holdings_tracker[sym]

        stale = [sym for sym in self._holdings_tracker if sym not in held]
        for sym in stale:
            del self._holdings_tracker[sym]

        kept = {sym for sym in self._holdings_tracker if sym in held}
        slots = self.max_holdings - len(kept)
        if slots <= 0:
            return {sym: 1.0 / self.max_holdings for sym in kept}

        df = universe_df.copy()
        if df.empty:
            return {}

        if "isST" in df.columns:
            df = df[df["isST"] == 0]
        if "name" in df.columns:
            df = df[~df["name"].astype(str).str.contains("ST|退", case=False, na=False)]
        df = df[~df.index.isin(kept)]

        if "today_paused" in df.columns:
            df = df[df["today_paused"] != True]

        close_col = "close" if "close" in df.columns else "收盘"
        open_col = "open" if "open" in df.columns else "开盘"
        pre_col = "pre_close" if "pre_close" in df.columns else "前收盘价"
        amount_col = "amount" if "amount" in df.columns else "成交额"
        vol_ratio_col = "vol_ratio_20" if "vol_ratio_20" in df.columns else "volume_ratio"

        required = [close_col, open_col, pre_col, amount_col, vol_ratio_col]
        if any(col not in df.columns for col in required):
            return {sym: 1.0 / self.max_holdings for sym in kept}

        for col in required:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=required)
        if df.empty:
            return {sym: 1.0 / self.max_holdings for sym in kept}

        pct = (df[close_col] - df[pre_col]) / df[pre_col]
        df = df[
            (df[vol_ratio_col] >= self.vol_threshold)
            & (df[vol_ratio_col] <= self.max_vol_threshold)
            & (pct >= self.min_pct_chg)
            & (pct <= self.max_pct_chg)
            & (df[close_col] > df[open_col])
            & (df[amount_col] >= self.min_amount)
        ]

        if df.empty:
            return {sym: 1.0 / self.max_holdings for sym in kept}

        df = df.assign(_score=df[vol_ratio_col] * 2 + pct * 100)
        picks = df.sort_values("_score", ascending=False).index.tolist()[:slots]
        for sym in picks:
            self._holdings_tracker[sym] = 0

        all_holdings = list(kept) + picks
        if not all_holdings:
            return {}
        weight = 1.0 / self.max_holdings
        return {sym: weight for sym in all_holdings}
