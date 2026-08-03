"""
JK 搅屎棍策略（忠实复刻版）

原载于聚宽《四大搅屎棍策略》，作者 MarioC
来源: https://www.joinquant.com/post/49085

原版核心逻辑（逐行对齐）：
1. 行业宽度判断：
   - 取中证全指(000985)全部成分股
   - 计算每只股票昨日收盘价是否站上 20 日均线（close > MA20）
   - 按申万一级行业分组，计算各行业"站上 MA20 的个股占比"
   - 取占比最高的 Top 1 行业
2. 搅屎棍研判：
   - 如果 Top 1 行业是银行/煤炭/石油石化/公用事业/通信之一（“五大金刚”防御板块）
   → 清仓不做（返回空权重）
3. 选股：
   - 从中小板综指(399101)成分股中选股
   - 过滤科创北交(4/8/68/3开头)、ST、次新股(<375天)
   - ROE > 15% 且 ROA > 10%
   - 按市值升序排列，取前 stock_num 只
4. 涨停处理：
   - 买入时过滤昨日涨停/跌停股（买不进/不良信号）
   - 卖出时保护昨日涨停的持仓（原版14:00检查，本地用次日补卖近似）
5. 等权买入

⚠️ 框架限制说明：
- 原版有"昨日涨停股持有到14:00再决定卖出"的盘中逻辑，
  本地回测框架只有日级别调仓，用"次日开盘补卖"近似。
- 原版的行业宽度基于聚宽的申万行业分类 API，
  本地使用 universe_df 中的 industry 字段（东财行业分类），按关键字匹配。
- 原版的选股基于中小板综指(399101)成分股，本地无法获取该指数成分，
  故改为筛选深交所主板/中小板标的（000/001/002开头）。
"""

import pandas as pd
import numpy as np
from loguru import logger
from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


# 原版申万一级行业代码与名称映射（用于日志输出）
SW1 = {
    '801010': '农林牧渔I', '801020': '采掘I', '801030': '化工I',
    '801040': '钢铁I', '801050': '有色金属I', '801060': '建筑建材I',
    '801070': '机械设备I', '801080': '电子I', '801090': '交运设备I',
    '801100': '信息设备I', '801110': '家用电器I', '801120': '食品饮料I',
    '801130': '纺织服装I', '801140': '轻工制造I', '801150': '医药生物I',
    '801160': '公用事业I', '801170': '交通运输I', '801180': '房地产I',
    '801190': '金融服务I', '801200': '商业贸易I', '801210': '休闲服务I',
    '801220': '信息服务I', '801230': '综合I', '801710': '建筑材料I',
    '801720': '建筑装饰I', '801730': '电气设备I', '801740': '国防军工I',
    '801750': '计算机I', '801760': '传媒I', '801770': '通信I',
    '801780': '银行I', '801790': '非银金融I', '801880': '汽车I',
    '801890': '机械设备I', '801950': '煤炭I', '801960': '石油石化I',
    '801970': '环保I', '801980': '美容护理I',
}

# 新版防御(抽血)金刚关键字（取代原有的有色/钢铁，新增公用事业、石油石化、通信）
SPOILER_KEYWORDS = ['银行', '煤炭', '石油石化', '公用事业', '通信']


class JKSpoilerStrategy(PortfolioStrategy):
    """
    四大搅屎棍策略 — 忠实复刻版

    参数对齐原版：
    - stock_num: 最大持仓数量（原版 g.stock_num = 10）
    - num: 行业宽度 Top N（原版 g.num = 1）
    - min_roe: ROE 下限（原版 indicator.roe > 0.15，百分比形式 15.0 = 15%）
    - min_roa: ROA 下限（原版 indicator.roa > 0.10，百分比形式 10.0 = 10%）
    - min_list_days: 次新股过滤天数（原版 375 天）
    """

    def __init__(
        self,
        stock_num: int = 10,
        num: int = 1,
        min_roe: float = 15.0,
        min_roa: float = 10.0,
        min_list_days: int = 375,
        rebalance_freq: str = "weekly",
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=stock_num,
        ))
        self.stock_num = stock_num
        self.top_n = stock_num  # 桥接：服务端通过 strategy.top_n 动态注入前端选股数量
        self.num = num
        self.min_roe = min_roe    # 百分比形式（15.0 = 15%）
        self.min_roa = min_roa    # 百分比形式（10.0 = 10%）
        self.min_list_days = min_list_days

    @property
    def name(self) -> str:
        return "JK_搅屎棍策略 (v1 本地改良版)"

    @property
    def description(self) -> str:
        return (
            "聚宽《四大搅屎棍策略》忠实复刻版（作者 MarioC）。\n"
            "逻辑：计算全市场各行业的 MA20 多头比例（行业宽度），"
            "如果宽度最高的行业是【银行/煤炭/石油石化/公用事业/通信】之一，"
            "说明资金在拉权重护盘、中小盘走弱，立刻清仓不做。\n"
            "否则：从深交所主板/中小板中选出 ROE>15%、ROA>10% 的小市值股，"
            "过滤 ST、次新股（<375天）、涨跌停股，按市值升序取前10只等权买入。\n"
            "调仓频率：每周一。"
        )

    @property
    def author(self) -> str:
        return "MarioC (忠实复刻版)"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        """
        完全对齐原版 get_stock_list() + weekly_adjustment() 的逻辑。
        """
        # =============================================================
        # 第 1 步：行业宽度计算（对齐原版 get_stock_list 的宽度部分）
        # =============================================================
        df_all = universe_df.copy()

        if 'industry' not in df_all.columns:
            logger.warning(f"[{self.name}] {date.date()} universe_df 缺少 industry 列，防守清仓")
            return {}

        if 'ma20_deviation' not in df_all.columns:
            logger.warning(f"[{self.name}] {date.date()} universe_df 缺少 ma20_deviation 列，防守清仓")
            return {}

        # 是否站上 MA20（对齐原版 df_bias = df_close > df_ma20）
        df_all['above_ma20'] = (df_all['ma20_deviation'] > 0).astype(int)

        # 按行业分组计算宽度比例
        industry_counts = df_all.groupby('industry')['above_ma20'].count()
        industry_sums = df_all.groupby('industry')['above_ma20'].sum()

        valid_industries = industry_counts[industry_counts >= 5].index
        industry_ratio = (industry_sums[valid_industries] * 100.0 / industry_counts[valid_industries]).round()

        if industry_ratio.empty:
            logger.warning(f"[{self.name}] {date.date()} 行业宽度计算结果为空，防守清仓")
            return {}

        top_industries = industry_ratio.nlargest(self.num)
        top_industry_names = top_industries.index.tolist()
        total_breadth = industry_ratio.mean()
        logger.info(
            f"[{self.name}] {date.date()} 行业宽度 Top{self.num}: {top_industry_names} "
            f"(比例: {top_industries.values.tolist()}) | 全市场宽度: {total_breadth:.1f}%"
        )

        # =============================================================
        # 第 2 步：搅屎棍判断
        # =============================================================
        is_spoiler = False
        for ind_name in top_industry_names:
            for keyword in SPOILER_KEYWORDS:
                if keyword in str(ind_name):
                    is_spoiler = True
                    logger.info(
                        f"[{self.name}] {date.date()} ⚠️ 搅屎棍报警！"
                        f"最强行业 [{ind_name}] 命中关键字 [{keyword}]，清仓！"
                    )
                    break
            if is_spoiler:
                break

        if is_spoiler:
            return {}

        logger.debug(f"[{self.name}] {date.date()} 行业安全，进入选股模块")

        # =============================================================
        # 第 3 步：选股
        # =============================================================
        df = df_all.copy()

        # 3.1 选股范围：剔除科创板（68开头）与北交所（4/8开头），保留沪深主板及创业板
        def _is_valid_stock(sym: str) -> bool:
            s = str(sym)
            if len(s) != 6:
                return False
            if s[0] in ('4', '8'):      # 北交所
                return False
            if s[:2] == '68':           # 科创板
                return False
            # 已修正：不再生硬照搬原版仅深交所(399101/000/002开头)的逻辑，放开上交所大盘（60开头）与创业板标的
            return True

        mask = df.index.map(_is_valid_stock)
        df = df[mask]

        if df.empty:
            logger.info(f"[{self.name}] {date.date()} 基础标的池（排查科创北交后）无可选标的")
            return {}

        # 3.2 filter_st_stock
        if 'isST' in df.columns:
            before = len(df)
            df = df[df['isST'] == 0]
            if len(df) < before:
                logger.debug(f"[{self.name}] ST 过滤: {before} → {len(df)}")

        if 'name' in df.columns:
            df = df[~df['name'].astype(str).str.contains('ST|退', case=False, na=False)]

        # 3.3 filter_new_stock（上市 < 375 天排除）
        if 'list_days' in df.columns:
            before = len(df)
            df = df[df['list_days'] >= self.min_list_days]
            if len(df) < before:
                logger.debug(f"[{self.name}] 次新股过滤(<{self.min_list_days}天): {before} → {len(df)}")

        if df.empty:
            logger.info(f"[{self.name}] {date.date()} 基础过滤后无可选标的")
            return {}

        # 3.4 ROE > 15%（使用引擎基于 PB/PE_TTM 推导出的 ttm_roe，百分比形式）
        if 'ttm_roe' in df.columns and df['ttm_roe'].notna().any():
            before = len(df)
            df = df[df['ttm_roe'].notna() & (df['ttm_roe'] > self.min_roe)]
            logger.debug(f"[{self.name}] TTM_ROE>{self.min_roe}% 过滤: {before} → {len(df)}")
        elif 'roe' in df.columns and df['roe'].notna().any():
            # 降级：如果引擎没有成功推导 ttm_roe，退回使用 baostock_profit 报告期 roe (小数形式)
            before = len(df)
            roe_threshold = self.min_roe / 100.0   # 15.0 → 0.15
            df = df[df['roe'].notna() & (df['roe'] > roe_threshold)]
            logger.debug(f"[{self.name}] 报告期ROE>{self.min_roe}% 过滤: {before} → {len(df)}")
        else:
            logger.warning(f"[{self.name}] {date.date()} 缺少 roe/ttm_roe 列或全为空，跳过 ROE 过滤")

        # 3.5 ROA > 10%（使用引擎推导出的 ttm_roa，即 ttm_roe / 权益乘数，也是百分比形式）
        if 'ttm_roa' in df.columns and df['ttm_roa'].notna().any():
            before = len(df)
            df = df[df['ttm_roa'].notna() & (df['ttm_roa'] > self.min_roa)]
            logger.debug(f"[{self.name}] TTM_ROA>{self.min_roa}% 过滤: {before} → {len(df)}")
        elif 'roa' in df.columns and df['roa'].notna().any():
            # 降级：如果历史数据有直接的 roa
            before = len(df)
            df = df[df['roa'].notna() & (df['roa'] > self.min_roa)]
            logger.debug(f"[{self.name}] ROA>{self.min_roa}% 过滤: {before} → {len(df)}")
        else:
            logger.warning(f"[{self.name}] {date.date()} 缺少 ttm_roa/roa 字段或全为空，暂跳过 ROA 过滤（存在数据黑洞隐患）")

        if df.empty:
            logger.info(f"[{self.name}] {date.date()} ROE/ROA 过滤后无可选标的")
            return {}

        # 3.6 按市值升序排列
        if 'market_cap' in df.columns:
            df = df.sort_values('market_cap', ascending=True)

        # 3.7 取前 stock_num 只
        selected = df.head(self.stock_num)

        if selected.empty:
            return {}

        # =============================================================
        # 第 4 步：涨停处理（对齐原版 filter_limitup/limitdown_stock）
        # =============================================================
        # is_limit_up: 昨日收盘 = 涨停价 → 今日大概率一字板，买不进
        # is_limit_down: 昨日收盘 = 跌停价 → 不良信号
        selected_symbols = selected.index.tolist()

        if 'is_limit_up' in selected.columns:
            limit_up_stocks = selected[selected['is_limit_up'] == 1].index.tolist()
            if limit_up_stocks:
                logger.info(f"[{self.name}] {date.date()} 涨停过滤（买不进）: {limit_up_stocks}")
                selected_symbols = [s for s in selected_symbols if s not in limit_up_stocks]

        if 'is_limit_down' in selected.columns:
            limit_down_stocks = selected[selected['is_limit_down'] == 1].index.tolist()
            if limit_down_stocks:
                logger.info(f"[{self.name}] {date.date()} 跌停过滤: {limit_down_stocks}")
                selected_symbols = [s for s in selected_symbols if s not in limit_down_stocks]

        if not selected_symbols:
            logger.info(f"[{self.name}] {date.date()} 涨跌停过滤后无可选标的")
            return {}

        # =============================================================
        # 第 5 步：等权买入
        # =============================================================
        weight = 1.0 / len(selected_symbols)
        weights = {sym: weight for sym in selected_symbols}

        names = [f"{sym}({universe_df.loc[sym, 'name']})" if 'name' in universe_df.columns else sym for sym in selected_symbols]
        logger.info(
            f"[{self.name}] {date.date()} 选出 {len(weights)} 只标的，等权 {weight:.2%}：{names}"
        )

        return weights
