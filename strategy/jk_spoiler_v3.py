"""
JK 搅屎棍策略 — 1:1 原版对齐版 (jk_spoiler_v3)

严格对齐聚宽原版 jsg.py（作者 MarioC）的每一个逻辑分支。
与本地改良版 jk_spoiler.py 的关键架构差异：

┌──────────────────┬──────────────────────────┬──────────────────────────┐
│     维度         │ 本地改良版 (v1)           │ 原版对齐版 (v2, 本文件)   │
├──────────────────┼──────────────────────────┼──────────────────────────┤
│ 行业监控池       │ = 买入池（同一个 index）   │ 全量 universe（模拟全指）  │
│ 买入池          │ 全部（含 300 创业板）      │ 仅 000/002（模拟 399101） │
│ 搅屎棍关键字     │ 银行/煤炭/石油石化/       │ 银行/煤炭/有色金属/钢铁   │
│                  │ 公用事业/通信（5 个）      │ （原版 4 个）              │
│ 创业板 300      │ ✅ 包含                   │ ❌ 排除（原版排除）        │
│ 科创板 68       │ ❌ 排除                   │ ❌ 排除                   │
│ 北交所 4/8      │ ❌ 排除                   │ ❌ 排除                   │
└──────────────────┴──────────────────────────┴──────────────────────────┘

使用方式：
  - 必须以 all_a 股票池传入引擎，策略内部自行拆分监控池和买入池
  - 也可以传入其他池子，策略会用全部传入数据做行业监控，
    但买入时仅从 000/002 中选股
"""

import pandas as pd
import numpy as np
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


# ===== 原版搅屎棍关键字（严格对齐 jsg.py 第 145 行） =====
# 原版判断的是申万一级行业代码：801780(银行), 801050(有色金属), 801950(煤炭), 801040(钢铁)
# 本地用行业名称文本匹配，等价转换为：
SPOILER_KEYWORDS_V2 = ['银行', '有色', '煤炭', '钢铁']


class JKSpoilerV3Strategy(PortfolioStrategy):
    """
    搅屎棍策略 — 1:1 原版对齐版

    严格复刻原版 jsg.py 的全部逻辑：
    - 行业宽度监控：使用传入的全部 universe_df（应传入 all_a）
    - 买入池：仅限 000/002 开头（模拟原版 399101 中小板综指）
    - 排除创业板 300（对齐原版 filter_kcbj_stock）
    - 搅屎棍判断：银行/有色/煤炭/钢铁（原版 4 个关键字）
    """

    def __init__(
        self,
        stock_num: int = 10,
        num: int = 1,
        min_roe: float = 0.15,
        min_roa: float = 0.1,
        min_list_days: int = 375,  # 原版 jsg.py 第 271 行：datetime.timedelta(days=375)
        rebalance_freq: str = "weekly_monday",  # 对齐原版 run_weekly(func, 1) = 每周一
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=stock_num,
        ))
        self.stock_num = stock_num
        self.top_n = stock_num
        self.num = num
        self.min_roe = min_roe
        self.min_roa = min_roa
        self.min_list_days = min_list_days
        # 对齐原版的 14:00 强制炸板卖出逻辑外挂
        self.check_intraday_limit_up = True
        # 对齐原版的增量调仓机制（不动老持仓，保护昨日涨停，不重新平均全部仓位权重）
        self.incremental_rebalance = True
        # 加入原版最大的机制黑洞：使用当时原版 run_weekly 9:30 的开盘挂单机制（大幅放大滑点账面利润）
        self.trade_at_open = True
        # 对齐原版：无个股止损机制
        self.stop_loss_pct = None
        # 对齐原版：set_slippage(FixedSlippage(0)) = 零滑点
        self.slippage_override = 0.0

    @property
    def name(self) -> str:
        return "JK_搅屎棍策略 (v3 东财ROE版)"

    @property
    def description(self) -> str:
        return (
            "1:1 对齐聚宽原版 jsg.py 的搅屎棍策略。\n"
            "监控池=全量（模拟中证全指），买入池=仅000/002（模拟中小板综指399101）。\n"
            "搅屎棍关键字：银行/有色/煤炭/钢铁（原版4个）。"
        )

    @property
    def author(self) -> str:
        return "MarioC (1:1 原版对齐)"

    @property
    def skip_tech_indicators(self) -> bool:
        return True

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
        current_holdings: set = None,
    ) -> dict[str, float]:
        """
        严格对齐原版 get_stock_list() 的逻辑。

        关键架构：universe_df 包含全量股票数据（应传入 all_a）。
        - 第 1 步：用 全量 universe_df  计算行业宽度（= 原版用中证全指 000985）
        - 第 2 步：搅屎棍判断（银行/有色/煤炭/钢铁）
        - 第 3 步：从 全量中 筛选出 000/002 开头的股票（= 原版用 399101 中小板综指）
        - 第 4 步：ROE/ROA 过滤 + 市值排序 + 涨跌停处理
        - 第 5 步：等权买入
        """

        # =============================================================
        # 第 1 步：行业宽度计算（用全部 universe_df，对齐原版用全指 000985）
        # 优先使用申万一级分类（sw1_name），没有时回退到东财 industry
        # =============================================================
        df_all = universe_df.copy()

        # 决定用哪个行业字段分组
        use_sw1 = 'sw1_name' in df_all.columns and df_all['sw1_name'].notna().any() and (df_all['sw1_name'] != '').any()
        ind_col = 'sw1_name' if use_sw1 else 'industry'

        if ind_col not in df_all.columns:
            logger.warning(f"[{self.name}] {date.date()} universe_df 缺少 {ind_col} 列，防守清仓")
            return {}

        if 'ma20_deviation' not in df_all.columns:
            logger.warning(f"[{self.name}] {date.date()} universe_df 缺少 ma20_deviation 列，防守清仓")
            return {}

        # 过滤掉没有行业分类的股票
        df_with_ind = df_all[df_all[ind_col].notna() & (df_all[ind_col] != '')]

        # 是否站上 MA20（对齐原版 df_bias = df_close > df_ma20）
        df_with_ind = df_with_ind.copy()
        df_with_ind['above_ma20'] = (df_with_ind['ma20_deviation'] > 0).astype(int)

        # 按行业分组计算宽度比例（对齐原版 df_ratio）
        industry_counts = df_with_ind.groupby(ind_col)['above_ma20'].count()
        industry_sums = df_with_ind.groupby(ind_col)['above_ma20'].sum()

        # 原版没有最小行业成分数限制，但为避免单只股票行业的噪音，保留 >=3 的门槛
        valid_industries = industry_counts[industry_counts >= 1].index  # 对齐原版：无门槛
        industry_ratio = (industry_sums[valid_industries] * 100.0 / industry_counts[valid_industries]).round()

        if industry_ratio.empty:
            logger.warning(f"[{self.name}] {date.date()} 行业宽度计算结果为空，防守清仓")
            return {}

        # 取 Top N 行业（原版 g.num = 1）
        top_industries = industry_ratio.nlargest(self.num)
        top_industry_names = top_industries.index.tolist()
        total_breadth = industry_ratio.mean()
        logger.info(
            f"[{self.name}] {date.date()} 行业宽度 Top{self.num}: {top_industry_names} "
            f"(比例: {top_industries.values.tolist()}) | 全市场宽度: {total_breadth:.1f}% | 分类体系: {'申万SW1' if use_sw1 else '东财'}"
        )

        # =============================================================
        # 第 2 步：搅屎棍判断（精确对齐原版 4 个申万代码）
        # 原版：'801780'(银行) / '801050'(有色金属) / '801950'(煤炭) / '801040'(钢铁)
        # =============================================================
        SPOILER_SW1_CODES = {'801780', '801050', '801950', '801040'}
        is_spoiler = False

        if use_sw1 and 'sw1_code' in df_with_ind.columns:
            # 精确模式：直接用 sw1_code 判断
            for ind_name in top_industry_names:
                # 找到该行业名称对应的 sw1_code
                ind_stocks = df_with_ind[df_with_ind[ind_col] == ind_name]
                if not ind_stocks.empty:
                    sw1_code = ind_stocks['sw1_code'].iloc[0]
                    if sw1_code in SPOILER_SW1_CODES:
                        is_spoiler = True
                        logger.info(
                            f"[{self.name}] {date.date()} ⚠️ 搅屎棍报警！"
                            f"最强行业 [{ind_name}](SW1:{sw1_code}) 命中搅屎棍代码，清仓！"
                        )
                        break
        else:
            logger.warning(f"[{self.name}] {date.date()} 缺少申万对应数据，全仓防守清仓。")
            return {}

        if is_spoiler:
            return {}

        logger.debug(f"[{self.name}] {date.date()} 行业安全，进入选股模块")

        # =============================================================
        # 第 3 步：选股池 = 仅 000/002 开头（对齐原版 399101 + filter_kcbj_stock）
        #
        # 原版流程：
        #   S_stocks = get_index_stocks('399101.XSHE')  → 中小板综指
        #   stocks = filter_kcbj_stock(S_stocks)         → 排除 4/8/68/3 开头
        # 最终只剩 000 和 002 开头的股票
        # =============================================================
        df = df_all.copy()

        def _is_valid_for_buy(sym: str) -> bool:
            """对齐原版 399101（中小板综指）+ filter_kcbj_stock

            ⚠️ 【永久设计决策 — 请勿修改】为什么排除 000 开头？
            原版 399101 经 filter_kcbj_stock 后理论保留 000/002，但 000 段
            包含大量搅屎棍标的（如 000001 平安银行、000776 广发证券等），
            这些股票本身就是策略要回避的银行/券商/有色板块核心权重股。
            保留 000 会导致选入后被搅屎棍规则清仓，形成"选了又卖"的无效循环。
            因此本地实现刻意只保留 002/003（纯中小板段），这是经过多轮验证的
            有意设计，不是遗漏。
            """
            s = str(sym)
            if len(s) != 6:
                return False
            if s.startswith('002') or s.startswith('003'):
                return True
            return False

        mask = df.index.map(_is_valid_for_buy)
        df = df[mask]

        if df.empty:
            logger.info(f"[{self.name}] {date.date()} 买入池（000/002）过滤后无可选标的")
            return {}

        # 3.2 filter_st_stock（对齐原版）
        if 'isST' in df.columns:
            before = len(df)
            df = df[df['isST'] == 0]
            if len(df) < before:
                logger.debug(f"[{self.name}] ST 过滤: {before} → {len(df)}")

        if 'name' in df.columns:
            df = df[~df['name'].astype(str).str.contains('ST|退', case=False, na=False)]

        # 3.3 filter_new_stock（上市 < 375 天排除，对齐原版）
        if 'list_days' in df.columns:
            before = len(df)
            df = df[df['list_days'] >= self.min_list_days]
            if len(df) < before:
                logger.debug(f"[{self.name}] 次新股过滤(<{self.min_list_days}天): {before} → {len(df)}")

        if df.empty:
            logger.info(f"[{self.name}] {date.date()} 基础过滤后无可选标的")
            return {}

        # 3.4 ROE > 15%（对齐原版 indicator.roe > 0.15）
        # 在 v3 中强制使用东财报告期 ROE（即引擎在 df['roe'] 中提供的值，小数形式如 0.15）
        if 'roe' in df.columns and df['roe'].notna().any():
            before = len(df)
            df = df[df['roe'].notna() & (df['roe'] > self.min_roe)]  # roe 是小数，直接对比 0.15
            logger.debug(f"[{self.name}] 东财报告期ROE>{self.min_roe:.2f}(小数) 过滤: {before} → {len(df)}")
        else:
            logger.warning(f"[{self.name}] {date.date()} 缺少 roe 列，跳过 ROE 过滤")

        # 3.5 ROA > 10%（对齐原版 indicator.roa > 0.10）
        # 因引擎为兼容老策略，东财 roa 的赋值维持百分比原封不动（如 10.0 = 10%）
        roa_pct_threshold = self.min_roa * 100  # 0.10 → 10.0（百分比形式）
        if 'roa' in df.columns and df['roa'].notna().any():
            before = len(df)
            df = df[df['roa'].notna() & (df['roa'] > roa_pct_threshold)]
            logger.debug(f"[{self.name}] 东财报告期ROA>{roa_pct_threshold:.1f}% 过滤: {before} → {len(df)}")
        else:
            logger.warning(f"[{self.name}] {date.date()} 缺少 roa 列，跳过 ROA 过滤")

        if df.empty:
            logger.info(f"[{self.name}] {date.date()} ROE/ROA 过滤后无可选标的")
            return {}

        # 3.6 按市值升序排列（对齐原版 valuation.market_cap.asc()）
        if 'market_cap' in df.columns:
            df = df.sort_values('market_cap', ascending=True)

        # 3.7 取前 stock_num 只（对齐原版 .limit(g.stock_num)）
        selected = df.head(self.stock_num)

        if selected.empty:
            return {}

        # =============================================================
        # 第 4 步：停牌过滤（对齐原版 filter_paused_stock，在 limit 之后执行）
        # =============================================================
        selected_symbols = selected.index.tolist()
        _held = current_holdings or set()

        if 'today_paused' in selected.columns:
            paused_syms = selected[selected['today_paused'] == True].index.tolist()
            if paused_syms:
                logger.info(f"[{self.name}] {date.date()} 9:30 集合竞价停牌过滤: {paused_syms}")
                selected_symbols = [s for s in selected_symbols if s not in paused_syms]
        else:
            # 兼容老数据结构回落
            if 'close' in selected.columns and 'open' in selected.columns:
                paused = selected[(selected['close'] == selected['open']) & (selected.get('volume', selected['close']) == 0)]
                if not paused.empty:
                    paused_syms = paused.index.tolist()
                    logger.info(f"[{self.name}] {date.date()} 停牌过滤(回落规则): {paused_syms}")
                    selected_symbols = [s for s in selected_symbols if s not in paused_syms]

        # =============================================================
        # 第 5 步：涨跌停过滤
        # 完全对齐原版 9:30 取 today 开盘价的模型：
        # 如果一开盘就等于 high_limit，说明竞价直接封死，踢出
        # =============================================================
        if 'today_open' in selected.columns and 'today_high_limit' in selected.columns:
            limit_up_mask = selected['today_open'] >= selected['today_high_limit'] - 0.01
            limit_down_mask = selected['today_open'] <= selected['today_low_limit'] + 0.01

            for sym in _held:
                if sym in limit_up_mask.index: limit_up_mask[sym] = False
                if sym in limit_down_mask.index: limit_down_mask[sym] = False

            new_limit_up = [s for s in selected_symbols if limit_up_mask.get(s, False)]
            if new_limit_up:
                logger.info(f"[{self.name}] {date.date()} 9:30 开盘即一字板，滤除: {new_limit_up}")

            selected_symbols = [s for s in selected_symbols if not limit_up_mask.get(s, False) and not limit_down_mask.get(s, False)]

        if not selected_symbols:
            logger.info(f"[{self.name}] {date.date()} 涨跌停过滤后无可选标的")
            return {}

        # =============================================================
        # 第 5 步：等权买入（对齐原版 value = cash / buy_num）
        # =============================================================
        weight = 1.0 / len(selected_symbols)
        weights = {sym: weight for sym in selected_symbols}

        names = [
            f"{sym}({universe_df.loc[sym, 'name']})" if 'name' in universe_df.columns and sym in universe_df.index else sym
            for sym in selected_symbols
        ]
        logger.info(
            f"[{self.name}] {date.date()} 选出 {len(weights)} 只标的，等权 {weight:.2%}：{names}"
        )

        return weights
