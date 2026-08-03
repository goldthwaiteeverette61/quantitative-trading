"""
北漂青年 1500 万微盘股量化策略

核心逻辑：
1. 目标股票池：小盘股、微盘股。
2. 财务过滤：通过 PB/PE 近似估算 ROE，排除非盈利垃圾股。
3. 技术面过滤：市值排序、20日均线乖离率、近期活跃度。
4. 散户行为因子：量化散户非理性行为并反向操作。

拆分为四个渐进版本：
- BeipiaoMicrocapV1: 纯微盘基线（仅市值排序）
- BeipiaoMicrocapV2: 微盘 + 财务安全垫（PB/PE 估算 ROE 过滤）
- BeipiaoMicrocapV3: 完整复刻最终版（叠加均线乖离与成交换手）
- BeipiaoMicrocapV4: 散户行为猎杀版（新增处置效应/炸板/暴涨过滤/动态市值上限）
"""
import pandas as pd
from loguru import logger
import numpy as np

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig


class BeipiaoMicrocapV1(PortfolioStrategy):
    """
    第一层：纯微盘基线
    规则：只过滤 ST 和退市，按流通市值升序排列，全A选前 N 只。
    """
    def __init__(self, top_n: int = 10, rebalance_freq: str = "weekly"):
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=top_n))
        self.top_n = top_n

    @property
    def name(self) -> str:
        return "北漂微盘_V1基准版"

    @property
    def description(self) -> str:
        return "纯微盘因子基准：过滤 ST 后按流通市值升序买入前 10 只，周频调仓。"

    def get_target_weights(self, date: pd.Timestamp, universe_df: pd.DataFrame) -> dict[str, float]:
        df = universe_df.copy()

        # 基础过滤：排除 ST 和停牌（无收盘价/无交易量）
        df = df[df['isST'] == 0]
        # 注意引擎构建的 df 里已经过滤了无数据的股，但我们可以加固一下
        if 'close' in df.columns:
            df = df[df['close'] > 2.0]  # 排除股价太低的壳股/退市整理期

        # 基本面兜底：排除亏损股（PE<=0）
        if 'pe' in df.columns:
            df = df[df['pe'] > 0]

        # 针对次新股：最好上市超过 150 天，避开新股巨大波动
        if 'list_days' in df.columns:
            df = df[df['list_days'] > 150]

        if 'market_cap' not in df.columns:
            logger.warning(f"[{self.name}] 缺少市值数据")
            return {}

        # 排除极微市值壳公司（流通市值 < 3亿）
        df = df[df['market_cap'] >= 3.0]

        # 排序：按市值从小到大
        df = df.sort_values('market_cap', ascending=True)
        selected = df.head(self.top_n)

        if len(selected) == 0:
            return {}

        # 等权分配
        weight = 0.95 / len(selected)
        weights = {sym: weight for sym in selected.index}
        return weights


class BeipiaoMicrocapV2(PortfolioStrategy):
    """
    第二层：微盘 + 财务安全垫
    规则：V1 基础上，加入基本面门槛（盈利股）。
    利用数据中的 PE 和 PB 估算 ROE。
    ROE ≈ PB / PE
    其中 PE=市值/净利润，PB=市值/净资产。所以 PB/PE = 净利润/净资产 = ROE
    """
    def __init__(self, top_n: int = 10, min_roe: float = 0.15, rebalance_freq: str = "weekly"):
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=top_n))
        self.top_n = top_n
        self.min_roe = min_roe  # 如 15%

    @property
    def name(self) -> str:
        return "北漂微盘_V2财务过滤"

    @property
    def description(self) -> str:
        return f"微盘 + 财务过滤：要求 ROE > {self.min_roe*100}% (用 PB/PE 近似)，按市值排序选前10。"

    def get_target_weights(self, date: pd.Timestamp, universe_df: pd.DataFrame) -> dict[str, float]:
        df = universe_df.copy()

        # 基础过滤
        df = df[df['isST'] == 0]
        if 'close' in df.columns:
            df = df[df['close'] > 1.0]
        if 'list_days' in df.columns:
            df = df[df['list_days'] > 150]

        if 'pe' not in df.columns or 'pb' not in df.columns or 'market_cap' not in df.columns:
            return {}

        # 核心：财务过滤
        # 要求盈利股，所以 PE 必须大于 0
        df = df[df['pe'] > 0]
        df = df[df['pb'] > 0]

        # 估算 ROE
        df['roe_est'] = df['pb'] / df['pe']

        # 限制 ROE 大于门槛
        df = df[df['roe_est'] >= self.min_roe]

        # 排序：按市值从小到大
        df = df.sort_values('market_cap', ascending=True)
        selected = df.head(self.top_n)

        if len(selected) == 0:
            return {}

        weight = 0.95 / len(selected)
        return {sym: weight for sym in selected.index}


class BeipiaoMicrocapV3(PortfolioStrategy):
    """
    第三层：完整复刻版
    规则：V2(微盘+财务)基础上叠加技术和交易面因子
    1. 20日均线乖离率 (Bias)：喜欢超跌反弹或某种特定形态
    2. 换手率：要求资金关注度
    """
    def __init__(self, top_n: int = 10, min_roe: float = 0.15, max_bias_20: float = 0.15, rebalance_freq: str = "weekly"):
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=top_n))
        self.top_n = top_n
        self.min_roe = min_roe
        self.max_bias_20 = max_bias_20  # 乖离率阈值，视频偏好某种回踩或超跌

    @property
    def name(self) -> str:
        return "北漂微盘_V3完整技术版"

    @property
    def description(self) -> str:
        return f"完整版：ROE > {self.min_roe*100}% + 20日乖离率限制 + 市值/活跃度综合打分。"

    def get_target_weights(self, date: pd.Timestamp, universe_df: pd.DataFrame) -> dict[str, float]:
        df = universe_df.copy()

        # 1. 基础过滤
        df = df[df['isST'] == 0]
        if 'close' in df.columns:
            df = df[df['close'] > 1.0]
        if 'list_days' in df.columns:
            df = df[df['list_days'] > 150]

        req_cols = ['pe', 'pb', 'market_cap', 'ma20_deviation', 'turnover']
        if not all(c in df.columns for c in req_cols):
            return {}

        # 2. 基本面过滤
        df = df[(df['pe'] > 0) & (df['pb'] > 0)]
        df['roe_est'] = df['pb'] / df['pe']
        df = df[df['roe_est'] >= self.min_roe]

        # 3. 技术面过滤：乖离率不能太高（太高说明已经暴涨过，风险大）
        # ma20_deviation 已经在引擎中计算好: (close - ma20) / ma20 * 100
        df = df[df['ma20_deviation'] <= (self.max_bias_20 * 100)]

        # 要求有一定的活跃度，日均换手 > 1%
        df = df[df['turnover'] > 1.0]

        if len(df) == 0:
            return {}

        # 4. 综合打分排序
        # 既然是微盘策略，市值因子是最主要的。
        # 我们用排名分来综合：市值越小越好，换手越大越好
        df['cap_rank'] = df['market_cap'].rank(ascending=True) # 市值小排名靠前
        df['turn_rank'] = df['turnover'].rank(ascending=False) # 换手高排名靠前

        # 综合分 = 市值排名 * 0.7 + 换手排名 * 0.3
        df['score_rank'] = df['cap_rank'] * 0.7 + df['turn_rank'] * 0.3

        # 按综合分从小到大排序
        df = df.sort_values('score_rank', ascending=True)
        selected = df.head(self.top_n)

        weight = 0.95 / len(selected)
        return {sym: weight for sym in selected.index}


class BeipiaoMicrocapV4(PortfolioStrategy):
    """
    第四层：散户行为猎杀版

    在 V3 基础上，新增以下散户行为因子与风控规则：

    新增因子：
    1. 处置效应因子 (disposition_ratio)：
       过去20日中"阳线但缩量"的天数占比 → 越高说明散户"赚了就跑"导致上方抛压释放完毕
       我们偏好此值较高的票（0.2~0.5），说明散户已经把筹码交出来了

    2. 炸板风险标记 (failed_limit_up)：
       当日最高价曾触及涨停但收盘回落 → 封板资金不坚定，游资出货信号
       持仓中出现此信号的票，下一调仓日优先清仓

    3. 市值动态上限：流通市值 < 30亿
       对齐微盘定义，避免策略漂移到中盘

    4. 去除暴涨过后的票：5日涨幅 > 20% 不入选
       警惕游资出货，避免高位接盘

    改进项：
    - 乖离率阈值从 V3 的 5% 放宽至 10%（实证显示5%过于严格，筛掉太多候选）
    """
    def __init__(
        self,
        top_n: int = 10,
        min_roe: float = 0.15,
        max_bias_20: float = 0.10,       # 放宽至 10%
        max_market_cap: float = 30.0,     # 流通市值上限(亿)
        max_5d_surge: float = 20.0,       # 5日涨幅上限(%)
        min_disposition: float = 0.15,    # 处置效应因子下限
        rebalance_freq: str = "weekly",
    ):
        super().__init__(RebalanceConfig(frequency=rebalance_freq, max_holdings=top_n))
        self.top_n = top_n
        self.min_roe = min_roe
        self.max_bias_20 = max_bias_20
        self.max_market_cap = max_market_cap
        self.max_5d_surge = max_5d_surge
        self.min_disposition = min_disposition

    @property
    def name(self) -> str:
        return "北漂微盘_V4散户猎杀版"

    @property
    def description(self) -> str:
        return (
            f"V4升级版：ROE>{self.min_roe*100}% + 市值<{self.max_market_cap}亿 + "
            f"乖离率<{self.max_bias_20*100}% + 处置效应因子 + 炸板过滤 + 暴涨过滤"
        )

    def get_target_weights(self, date: pd.Timestamp, universe_df: pd.DataFrame) -> dict[str, float]:
        df = universe_df.copy()

        # ===== 1. 基础过滤 =====
        df = df[df['isST'] == 0]
        if 'close' in df.columns:
            df = df[df['close'] > 1.0]
        if 'list_days' in df.columns:
            df = df[df['list_days'] > 150]

        req_cols = ['pe', 'pb', 'market_cap', 'ma20_deviation', 'turnover',
                    'momentum_5d', 'disposition_ratio', 'failed_limit_up']
        if not all(c in df.columns for c in req_cols):
            # 如果引擎还没有 V4 扩展字段，降级到 V3 逻辑
            logger.warning(f"[{self.name}] 缺少 V4 扩展字段，部分因子将被跳过")
            # 标记缺失的列，后续跳过对应过滤
            missing = [c for c in req_cols if c not in df.columns]
        else:
            missing = []

        # ===== 2. 基本面过滤 =====
        if 'pe' in df.columns and 'pb' in df.columns:
            df = df[(df['pe'] > 0) & (df['pb'] > 0)]
            df['roe_est'] = df['pb'] / df['pe']
            df = df[df['roe_est'] >= self.min_roe]

        # ===== 3. 市值动态上限 =====
        if 'market_cap' in df.columns:
            df = df[df['market_cap'] <= self.max_market_cap]

        # ===== 4. 技术面过滤 =====
        # 4a. 乖离率上限（放宽至10%）
        if 'ma20_deviation' in df.columns:
            df = df[df['ma20_deviation'] <= (self.max_bias_20 * 100)]

        # 4b. 要求活跃度
        if 'turnover' in df.columns:
            df = df[df['turnover'] > 1.0]

        # ===== 5. 散户行为因子过滤 =====
        # 5a. 去除近5日暴涨票（游资出货风险）
        if 'momentum_5d' in df.columns and 'momentum_5d' not in missing:
            df = df[df['momentum_5d'] <= self.max_5d_surge]

        # 5b. 去除炸板票（封板不坚定 = 游资出货）
        if 'failed_limit_up' in df.columns and 'failed_limit_up' not in missing:
            df = df[df['failed_limit_up'] == 0]

        # 5c. 处置效应因子：要求有一定的"散户交出筹码"信号
        if 'disposition_ratio' in df.columns and 'disposition_ratio' not in missing:
            df = df[df['disposition_ratio'] >= self.min_disposition]

        if len(df) == 0:
            return {}

        # ===== 6. 综合打分排序 =====
        # 市值越小越好（核心因子）
        df['cap_rank'] = df['market_cap'].rank(ascending=True)
        # 换手越大越好（要有资金参与）
        df['turn_rank'] = df['turnover'].rank(ascending=False)
        # 处置效应越高越好（散户筹码释放越充分）
        if 'disposition_ratio' in df.columns and 'disposition_ratio' not in missing:
            df['disp_rank'] = df['disposition_ratio'].rank(ascending=False)
        else:
            df['disp_rank'] = 0

        # 综合分：市值 50% + 换手 25% + 处置效应 25%
        df['score_rank'] = df['cap_rank'] * 0.50 + df['turn_rank'] * 0.25 + df['disp_rank'] * 0.25

        df = df.sort_values('score_rank', ascending=True)
        selected = df.head(self.top_n)

        weight = 0.95 / len(selected)
        return {sym: weight for sym in selected.index}
