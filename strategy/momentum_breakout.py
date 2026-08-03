"""
动能突破策略（Momentum Breakout）

基于日线级别的量价共振突破信号，捕捉强势股启动瞬间。

核心逻辑来源：
    对 002962（五方光电）2026年4月突破行情的实盘复盘，提炼出四条
    可量化、可回测、可证伪的买入规则。

买入条件（四条同时满足）：
    1. 创 20 日新高：当日收盘价 >= 过去 20 个交易日最高收盘价
    2. 放量异动：当日成交量 >= 近 20 日均量的 vol_threshold 倍（默认 3.0）
    3. 涨幅确认：当日涨幅 > min_pct_chg（默认 5%）
    4. 阳线实体：收盘价 > 开盘价

卖出规则：
    - 持有期到期：买入后持有 max_hold_days 个交易日自动卖出（默认 5）
    - 止损：买入价下跌 stop_loss_pct 后触发引擎级止损（默认 -5%）

设计要点：
    - 调仓频率为 daily（每个交易日扫描），确保不遗漏任何突破信号
    - 策略内部维护 _holdings_tracker 字典，记录每只持仓股的已持有天数
    - 空仓位由新产生的突破信号填入，满仓时不再追加
"""

import pandas as pd
import numpy as np
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class MomentumBreakoutStrategy(PortfolioStrategy):
    """
    日线级动能突破策略

    通过四条严格的量价共振条件筛选突破标的，
    配合固定持有期 + 止损的退出机制。
    """

    def __init__(
        self,
        max_holdings: int = 5,
        vol_threshold: float = 3.0,
        min_pct_chg: float = 0.05,
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
        self.min_pct_chg = min_pct_chg
        self.max_hold_days = max_hold_days
        self.min_list_days = min_list_days
        # 引擎级止损
        self.stop_loss_pct = stop_loss_pct
        # 持仓天数追踪器：{symbol: 已持有天数}
        self._holdings_tracker: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "动能突破策略 (Momentum Breakout)"

    @property
    def description(self) -> str:
        return (
            f"日线级量价共振突破策略。\n"
            f"买入：20日新高 + {self.vol_threshold}倍放量 + "
            f"{self.min_pct_chg:.0%}涨幅 + 阳线。\n"
            f"卖出：持有{self.max_hold_days}日后自动卖出，"
            f"或触发{self.stop_loss_pct:.0%}止损。"
        )

    @property
    def author(self) -> str:
        return "复盘推演"

    @property
    def needs_tech_indicators(self) -> bool:
        # 需要 is_20d_high、vol_ratio_20 等技术指标
        return True

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
        current_holdings: set = None,
    ) -> dict[str, float]:
        """
        每日扫描全市场，寻找符合四条件的动能突破标的。

        工作流程：
        1. 更新持仓天数，踢出到期（> max_hold_days）的持仓
        2. 保留未到期的现有持仓
        3. 从全市场筛选新的突破信号，填入空仓位
        4. 返回合并的目标权重
        """
        _held = current_holdings or set()
        date_str = date.strftime('%Y-%m-%d')

        # =============================================================
        # 第 1 步：持仓期管理 — 更新天数，踢出到期持仓
        # =============================================================
        expired = []
        for sym in list(self._holdings_tracker.keys()):
            self._holdings_tracker[sym] += 1
            if self._holdings_tracker[sym] > self.max_hold_days:
                expired.append(sym)
                del self._holdings_tracker[sym]

        if expired:
            logger.info(
                f"[{self.name}] {date_str} 持有期到期卖出: {expired}"
            )

        # 清理不在实际持仓中的跟踪记录（引擎可能已因止损卖出）
        stale = [s for s in self._holdings_tracker if s not in _held]
        for s in stale:
            del self._holdings_tracker[s]

        # 当前有效持仓（未到期 + 引擎未止损）
        kept_holdings = {
            sym for sym in self._holdings_tracker if sym in _held
        }

        # =============================================================
        # 第 2 步：扫描全市场，寻找新的突破信号
        # =============================================================
        available_slots = self.max_holdings - len(kept_holdings)

        new_signals = []
        if available_slots > 0:
            df = universe_df.copy()

            # --- 基础过滤 ---
            # 排除 ST
            if 'isST' in df.columns:
                df = df[df['isST'] == 0]
            if 'name' in df.columns:
                df = df[~df['name'].astype(str).str.contains(
                    'ST|退', case=False, na=False
                )]

            # 排除次新股
            if 'list_days' in df.columns:
                df = df[df['list_days'] >= self.min_list_days]

            # 排除已持有的股票（不重复买入）
            df = df[~df.index.isin(kept_holdings)]

            # 排除停牌
            if 'today_paused' in df.columns:
                df = df[df['today_paused'] != True]

            # --- 四条件筛选 ---
            # 条件 1：创 20 日新高
            if 'is_20d_high' in df.columns:
                df = df[df['is_20d_high'] == 1]
            else:
                logger.warning(
                    f"[{self.name}] {date_str} 缺少 is_20d_high 列，跳过"
                )
                df = df.head(0)

            # 条件 2：放量异动
            if 'vol_ratio_20' in df.columns and not df.empty:
                df = df[df['vol_ratio_20'] >= self.vol_threshold]
            else:
                if df.empty:
                    pass
                else:
                    logger.warning(
                        f"[{self.name}] {date_str} 缺少 vol_ratio_20 列，跳过"
                    )
                    df = df.head(0)

            # 条件 3：日涨幅 > min_pct_chg
            if not df.empty:
                if 'close' in df.columns and 'pre_close' in df.columns:
                    pct_chg = (
                        (df['close'] - df['pre_close']) / df['pre_close']
                    )
                    df = df[pct_chg > self.min_pct_chg]
                else:
                    logger.warning(
                        f"[{self.name}] {date_str} "
                        f"缺少 close/pre_close 列，跳过涨幅过滤"
                    )

            # 条件 4：阳线（收盘 > 开盘）
            if not df.empty:
                if 'today_open' in df.columns:
                    df = df[df['close'] > df['today_open']]
                elif 'open' in df.columns:
                    df = df[df['close'] > df['open']]

            # --- 排除涨停无法买入的标的 ---
            if not df.empty:
                if ('today_open' in df.columns
                        and 'today_high_limit' in df.columns):
                    limit_up = (
                        df['today_open'] >= df['today_high_limit'] - 0.01
                    )
                    df = df[~limit_up]

            # --- 按量比降序排列，优先选择资金涌入最猛的 ---
            if not df.empty and 'vol_ratio_20' in df.columns:
                df = df.sort_values('vol_ratio_20', ascending=False)

            new_signals = df.index.tolist()[:available_slots]

            if new_signals:
                names_info = []
                for sym in new_signals:
                    n = ''
                    if 'name' in universe_df.columns:
                        n = f"({universe_df.loc[sym, 'name']})"
                    vr = df.loc[sym, 'vol_ratio_20'] if sym in df.index else 0
                    pct = 0
                    if 'close' in universe_df.columns and 'pre_close' in universe_df.columns:
                        pct = (
                            (universe_df.loc[sym, 'close']
                             - universe_df.loc[sym, 'pre_close'])
                            / universe_df.loc[sym, 'pre_close'] * 100
                        )
                    names_info.append(
                        f"{sym}{n} 量比:{vr:.1f} 涨幅:{pct:.1f}%"
                    )
                logger.info(
                    f"[{self.name}] {date_str} "
                    f"🔥 发现 {len(new_signals)} 个突破信号: {names_info}"
                )

                # 注册到持仓追踪器
                for sym in new_signals:
                    self._holdings_tracker[sym] = 0

        # =============================================================
        # 第 3 步：组装目标权重
        # =============================================================
        all_holdings = list(kept_holdings) + new_signals

        if not all_holdings:
            return {}

        weight = 1.0 / self.max_holdings
        weights = {sym: weight for sym in all_holdings}

        logger.debug(
            f"[{self.name}] {date_str} "
            f"目标持仓 {len(weights)} 只 "
            f"(保留:{len(kept_holdings)} 新增:{len(new_signals)})，"
            f"单只权重 {weight:.2%}"
        )

        return weights
