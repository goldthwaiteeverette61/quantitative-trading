"""
组合回测引擎 — V2 Portfolio Backtest

核心流程：
1. 按调仓日遍历
2. 调用 strategy.get_target_weights() 获取目标仓位
3. 先卖后买，计算换仓成本
4. 记录每日组合净值、持仓快照
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from config.settings import config
from data.trading_calendar import filter_a_share_trading_dates


@dataclass
class HoldingSnapshot:
    """单只持仓快照"""
    symbol: str
    name: str
    shares: int
    cost_price: float       # 持仓成本
    current_price: float    # 当前价格
    market_value: float     # 持仓市值
    weight: float           # 在组合中的权重
    pnl_pct: float          # 浮动盈亏 %


@dataclass
class RebalanceRecord:
    """单次调仓记录"""
    date: pd.Timestamp
    sells: list[dict] = field(default_factory=list)    # [{"symbol", "shares", "price", "cost"}]
    buys: list[dict] = field(default_factory=list)     # [{"symbol", "shares", "price", "cost"}]
    total_cost: float = 0.0                            # 本次调仓总成本
    turnover_pct: float = 0.0                          # 换手率 %


@dataclass
class PortfolioBacktestResult:
    """组合回测结果"""
    strategy_name: str
    index_name: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    initial_capital: float

    # 收益
    final_nav: float = 0.0              # 最终净值
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0

    # 风险
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    volatility_pct: float = 0.0         # 年化波动率

    # 组合特有指标
    total_rebalances: int = 0           # 调仓次数
    avg_turnover_pct: float = 0.0       # 平均换手率
    avg_holdings: float = 0.0           # 平均持仓数
    total_trade_cost: float = 0.0       # 累计交易成本

    # 详细数据
    nav_series: list[dict] = field(default_factory=list)         # [{"date", "nav"}]
    rebalance_records: list[RebalanceRecord] = field(default_factory=list)
    final_holdings: list[HoldingSnapshot] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'═' * 60}",
            f"  组合回测报告: {self.strategy_name}",
            f"{'═' * 60}",
            f"  指数基准:  {self.index_name}",
            f"  时间范围:  {self.start_date.date()} ~ {self.end_date.date()}",
            f"  初始资金:  ¥{self.initial_capital:,.0f}",
            f"  最终净值:  {self.final_nav:.4f}",
            f"{'─' * 60}",
            f"  总收益率:     {self.total_return_pct:+.2f}%",
            f"  年化收益:     {self.annual_return_pct:+.2f}%",
            f"  最大回撤:     {self.max_drawdown_pct:.2f}%",
            f"  夏普比率:     {self.sharpe_ratio:.2f}",
            f"  年化波动率:   {self.volatility_pct:.2f}%",
            f"{'─' * 60}",
            f"  调仓次数:     {self.total_rebalances}",
            f"  平均换手率:   {self.avg_turnover_pct:.1f}%",
            f"  平均持仓数:   {self.avg_holdings:.1f}",
            f"  累计交易成本: ¥{self.total_trade_cost:,.0f}",
            f"{'═' * 60}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "index_name": self.index_name,
            "start_date": str(self.start_date.date()),
            "end_date": str(self.end_date.date()),
            "initial_capital": self.initial_capital,
            "final_nav": round(self.final_nav, 4),
            "total_return_pct": round(self.total_return_pct, 2),
            "annual_return_pct": round(self.annual_return_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "volatility_pct": round(self.volatility_pct, 2),
            "total_rebalances": self.total_rebalances,
            "avg_turnover_pct": round(self.avg_turnover_pct, 1),
            "avg_holdings": round(self.avg_holdings, 1),
            "total_trade_cost": round(self.total_trade_cost, 0),
            "nav_series": self.nav_series,
            "final_holdings": [
                {
                    "symbol": h.symbol, "name": h.name,
                    "weight": round(h.weight, 4),
                    "pnl_pct": round(h.pnl_pct, 2),
                } for h in self.final_holdings
            ],
        }


class PortfolioBacktestEngine:
    """
    组合回测引擎

    模拟多股票组合的调仓换股过程，精确计算交易成本。
    """

    def __init__(
        self,
        initial_capital: float | None = None,
        commission_rate: float | None = None,
        slippage_rate: float | None = None,
        stamp_tax_rate: float | None = None,
        stop_loss_pct: float | None = -0.08,
    ):
        cfg = config.portfolio
        self.initial_capital = initial_capital if initial_capital is not None else cfg.initial_capital
        self.commission = commission_rate if commission_rate is not None else cfg.commission_rate
        self.slippage = slippage_rate if slippage_rate is not None else cfg.slippage_rate
        self.stamp_tax = stamp_tax_rate if stamp_tax_rate is not None else cfg.stamp_tax_rate
        self.stop_loss_pct = stop_loss_pct

        # 预加载股票基础信息 (IPO, Industry, Name)
        self.ipo_dict: dict[str, pd.Timestamp] = {}
        self.industry_dict: dict[str, str] = {}
        self.name_dict: dict[str, str] = {}
        self._load_stock_basics()

        # 预加载 ROE 字典（最新报告期的加权平均 ROE，小数形式，如 0.15 = 15%）
        self.roe_dict: dict[str, float] = {}
        self._load_roe_data()

        # 加载东财 ROA 历史数据（防未来函数版）
        self._em_roa_dict: dict[str, pd.DataFrame] = {}
        self._load_eastmoney_roa_data()

        # 预加载权益乘数字典（用于推导 TTM ROA = TTM ROE / 权益乘数）
        self.equity_multiplier_dict: dict[str, float] = {}
        self._load_equity_multiplier_data()

        # 预加载申万一级行业分类（从 SQLite sw1_industry 表，精确对齐聚宽 SW1 体系）
        self.sw1_dict: dict[str, dict] = {}  # {stock_code: {'sw1_code': '801780', 'sw1_name': '银行'}}
        self._load_sw1_industry()

    def _load_stock_basics(self):
        """从 MySQL stock_basic 表加载 IPO 日期、行业、名称等静态信息"""
        try:
            from data.database import DatabaseManager
            db = DatabaseManager()
            conn = db._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT symbol, ipo_date, industry, code_name FROM stock_basic")
                for row in cursor.fetchall():
                    sym, ipo, ind, name = row[0], row[1], row[2], row[3]
                    if ipo and ipo.strip():
                        try:
                            self.ipo_dict[sym] = pd.Timestamp(ipo)
                        except Exception:
                            pass
                    self.industry_dict[sym] = ind if ind else '未知'
                    self.name_dict[sym] = name if name else sym
            finally:
                conn.close()
            logger.debug(f"从 MySQL 加载 {len(self.ipo_dict)} 只股票的基础信息")
        except Exception as e:
            logger.warning(f"加载股票基础信息失败: {e}")

    def _load_roe_data(self):
        """从 baostock_profit 表加载最新报告期的 ROE 数据

        每只股票只取 stat_date 最大的那条记录的 roe_avg。
        roe_avg 是报告期累计的加权平均 ROE，小数形式（如 0.15 = 15%）。
        """
        try:
            from data.database import DatabaseManager
            db = DatabaseManager()
            conn = db._get_conn()
            try:
                cursor = conn.cursor()
                # 子查询：每只股票取最新报告期的 ROE
                cursor.execute("""
                    SELECT bp.symbol, bp.roe_avg
                    FROM baostock_profit bp
                    INNER JOIN (
                        SELECT symbol, MAX(stat_date) AS max_date
                        FROM baostock_profit
                        WHERE roe_avg IS NOT NULL
                        GROUP BY symbol
                    ) latest ON bp.symbol = latest.symbol AND bp.stat_date = latest.max_date
                """)
                for row in cursor.fetchall():
                    sym, roe = row[0], row[1]
                    if roe is not None:
                        self.roe_dict[sym] = float(roe)
            finally:
                conn.close()
            logger.debug(f"从 baostock_profit 加载 {len(self.roe_dict)} 只股票的 ROE 数据")
        except Exception as e:
            logger.warning(f"加载 ROE 数据失败: {e}")

    def _load_eastmoney_roa_data(self):
        """加载东财 ROA 历史数据到引擎"""
        try:
            from data.database import DatabaseManager
            db = DatabaseManager()
            conn = db._get_conn()
            try:
                # 取出字段
                df = pd.read_sql("SELECT symbol, stat_date, roa, roe FROM eastmoney_roa", conn)
                if not df.empty:
                    df['stat_date'] = pd.to_datetime(df['stat_date'])
                    # ========= 方案 A：自适应实盘模式 =========
                    # 核心逻辑：
                    #   - 历史财报（stat_date 距今 > 60 天）：+60 天延迟，防止回测中的未来函数
                    #   - 近期财报（stat_date 距今 <= 60 天）：直接使用 stat_date 作为 pub_date
                    #     理由：如果爬虫今天能从东财拉到这条数据，说明它已经在物理世界公开发布了，
                    #     数据库中不可能存在"明天才发布"的财报，物理世界本身就是防未来的最强护盾。
                    now = pd.Timestamp.now()
                    cutoff = now - pd.Timedelta(days=60)
                    # 历史数据：保守 +60 天
                    hist_mask = df['stat_date'] <= cutoff
                    df.loc[hist_mask, 'pub_date'] = df.loc[hist_mask, 'stat_date'] + pd.Timedelta(days=60)
                    # 近期数据：直接放行（实盘就绪）
                    df.loc[~hist_mask, 'pub_date'] = df.loc[~hist_mask, 'stat_date']
                    df = df.sort_values('pub_date')
                    # 按股票打散成 dict 以便后续 O(1) 取用
                    self._em_roa_dict = dict(tuple(df.groupby('symbol')))
            finally:
                conn.close()
            logger.debug(f"加载了 {len(self._em_roa_dict)} 只股票的东财历史 ROA/ROE 数据")
        except Exception as e:
            logger.warning(f"加载东财 ROA 历史数据失败: {e}")

    def _load_equity_multiplier_data(self):
        """从 baostock_balance 表加载由于推导 TTM ROA 所需的权益乘数"""
        try:
            from data.database import DatabaseManager
            db = DatabaseManager()
            conn = db._get_conn()
            try:
                cursor = conn.cursor()
                # 兼容即使表不存在也不报错
                cursor.execute("SHOW TABLES LIKE 'baostock_balance'")
                if cursor.fetchone():
                    cursor.execute("SELECT symbol, asset_to_equity FROM baostock_balance WHERE asset_to_equity IS NOT NULL")
                    for row in cursor.fetchall():
                        sym, val = row[0], row[1]
                        if val:
                            self.equity_multiplier_dict[sym] = float(val)
                else:
                    logger.warning("baostock_balance 表不存在，TTM ROA 将无法推导")
            finally:
                conn.close()
            logger.debug(f"加载 {len(self.equity_multiplier_dict)} 只股票的权益乘数数据")
        except Exception as e:
            logger.warning(f"加载权益乘数数据失败: {e}")

    def _load_sw1_industry(self):
        """从 MySQL sw1_industry 表加载申万一级行业分类，精确对齐聚宽 SW1 体系"""
        try:
            from data.database import DatabaseManager
            db = DatabaseManager()
            conn = db._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("SHOW TABLES LIKE 'sw1_industry'")
                if not cursor.fetchone():
                    logger.info("sw1_industry 表不存在，将使用中文关键字模糊匹配行业")
                    return
                cursor.execute("SELECT stock_code, sw1_code, sw1_name FROM sw1_industry")
                for row in cursor.fetchall():
                    code = str(row[0])
                    # ⚠️ key 必须是纯 6 位数字（如 000001），与 price_data.keys() 格式一致
                    # 不要加 sh./sz./bj. 前缀，否则 sw1_dict.get(sym) 永远匹配不上
                    self.sw1_dict[code] = {'sw1_code': row[1], 'sw1_name': row[2]}
                logger.info(f"从 MySQL 加载 {len(self.sw1_dict)} 只股票的申万一级行业分类")
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"加载申万行业分类失败: {e}")

    def _enrich_with_tech_indicators(self, price_data: dict[str, pd.DataFrame], stop_event=None):
        """预计算技术指标并添加到每只股票的 DataFrame（原地修改）"""
        import time as _t
        t0 = _t.time()
        count, failed = 0, 0
        for sym, df in price_data.items():
            if stop_event and stop_event.is_set(): return
            try:
                c = df['收盘'].astype(float); h = df['最高'].astype(float)
                lo = df['最低'].astype(float); v = df['成交量'].astype(float)
                if len(c) < 30: continue
                df['ema5'] = c.ewm(span=5, adjust=False).mean()
                df['ema10'] = c.ewm(span=10, adjust=False).mean()
                df['ema20'] = c.ewm(span=20, adjust=False).mean()
                e12 = c.ewm(span=12, adjust=False).mean(); e26 = c.ewm(span=26, adjust=False).mean()
                ml = e12 - e26; sig = ml.ewm(span=9, adjust=False).mean()
                df['macd_hist'] = ml - sig; df['macd_hist_slope'] = df['macd_hist'].diff(3) / 3.0
                d = c.diff(); g = d.clip(lower=0); l = (-d).clip(lower=0)
                ag = g.ewm(span=14, min_periods=14, adjust=False).mean()
                al = l.ewm(span=14, min_periods=14, adjust=False).mean()
                df['rsi'] = (100 - 100/(1+ag/al.replace(0, np.nan))).fillna(50)
                bm = c.rolling(20).mean(); bs = c.rolling(20).std()
                bw = 4*bs; df['bb_pct_b'] = np.where(bw>0, (c-(bm-2*bs))/bw, 0.5)
                tr = pd.concat([h-lo, (h-c.shift(1)).abs(), (lo-c.shift(1)).abs()], axis=1).max(axis=1)
                atr = tr.rolling(14).mean(); ap5 = atr.shift(1).rolling(5).mean()
                df['atr_breakout'] = np.where(ap5>0, (atr>ap5).astype(int), 0)
                df['atr_compress'] = ((atr<atr.shift(1))&(atr.shift(1)<atr.shift(2))).astype(int)
                pdm = h.diff().clip(lower=0); mdm = (-lo.diff()).clip(lower=0)
                cd = pdm>mdm; p_adj = pdm.where(cd,0); m_adj = mdm.where(~cd,0)
                a14 = tr.ewm(span=14, adjust=False).mean().replace(0, np.nan)
                pi = 100*p_adj.ewm(span=14, adjust=False).mean()/a14
                mi = 100*m_adj.ewm(span=14, adjust=False).mean()/a14
                dx = 100*(pi-mi).abs()/(pi+mi).replace(0, np.nan)
                df['adx'] = dx.ewm(span=14, adjust=False).mean().fillna(0)
                v5 = v.shift(1).rolling(5).mean()
                v20 = v.shift(1).rolling(20).mean()
                df['vol_ratio'] = np.where(v5>0, v/v5, 1.0)
                df['vol_ratio_20'] = np.where(v20>0, v/v20, 1.0)
                obv = (np.sign(c.diff().fillna(0))*v).cumsum()
                df['obv_slope'] = obv.diff(5).fillna(0)
                df['is_20d_high'] = (c>=c.rolling(20).max()).astype(int)
                ab = df['ema5']>df['ema20']; cr = ab & ~ab.shift(1).fillna(True)
                df['ema_golden_cross'] = cr.rolling(5, min_periods=1).max().fillna(0).astype(int)
                for col in ['ema5','ema10','ema20','macd_hist','macd_hist_slope','rsi',
                            'bb_pct_b','atr_breakout','atr_compress','adx','vol_ratio','vol_ratio_20',
                            'obv_slope','is_20d_high','ema_golden_cross']:
                    df[col] = df[col].fillna(0)
                count += 1
            except Exception as e:
                failed += 1
                if failed <= 3: logger.warning(f"技术指标计算失败 {sym}: {e}")
            if count % 500 == 0 and count > 0:
                logger.info(f"技术指标预计算进度: {count}/{len(price_data)}")
        logger.info(f"技术指标预计算完成: {count} 只成功, {failed} 只失败, 耗时 {_t.time()-t0:.1f}s")

    def _precompute_factors(self, price_data: dict[str, pd.DataFrame], stop_event=None):
        """向量化预计算基础因子，添加为 DataFrame 列并建立日期字符串索引"""
        import time as _t
        t0 = _t.time()
        self._indexed_data: dict[str, pd.DataFrame] = {}
        count = 0
        for sym, df in price_data.items():
            if stop_event and stop_event.is_set(): return
            try:
                n = len(df)
                if n < 10: continue
                c = df['收盘'].astype(float)
                op = df['开盘'].astype(float) if '开盘' in df.columns else c
                vol = df['成交量'].astype(float) if '成交量' in df.columns else pd.Series(0, index=df.index)
                # 动量
                df['_m5'] = c.pct_change(5) * 100
                df['_m20'] = c.pct_change(20) * 100
                df['_m60'] = c.pct_change(60) * 100
                # MA偏离
                ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean()
                df['_ma20d'] = np.where(ma20 > 0, (c - ma20) / ma20 * 100, 0)
                df['_ma60d'] = np.where(ma60 > 0, (c - ma60) / ma60 * 100, 0)
                # 换手率均值
                tr_col = df['换手率'] if '换手率' in df.columns else pd.Series(0, index=df.index)
                df['_avg_tr'] = tr_col.rolling(20, min_periods=5).mean()
                # 波动率
                rets = c.pct_change()
                df['_vol20'] = rets.rolling(20).std() * (252**0.5) * 100
                df['_vol60'] = rets.rolling(60).std() * (252**0.5) * 100
                # 散户因子
                up = (c > op).astype(int); vs = (vol < vol.shift(1)).astype(int)
                df['_disp'] = (up * vs).rolling(20, min_periods=10).mean()
                pc = c.shift(1); hi = df['最高'].astype(float) if '最高' in df.columns else c
                lp = 0.20 if (sym.startswith('3') or sym.startswith('68')) else 0.10
                lu_p = pc * (1 + lp * 0.98)
                df['_f_lu'] = ((hi >= lu_p) & (c < lu_p * 0.99)).astype(int)
                av20 = vol.shift(5).rolling(20).mean(); av5 = vol.rolling(5).mean()
                df['_surge'] = np.where((df['_m5'] < -3) & (av20 > 0), av5 / av20, 0)
                df['_mdiv'] = (df['_m5'] - df['_m20']).abs()
                # 涨跌停价计算（供搅屎棍等策略使用）
                # A 股涨跌停幅度：普通股 ±10%，ST ±5%，科创/创业板 ±20%
                _pre_close = df['前收盘价'].astype(float) if '前收盘价' in df.columns else pc
                _is_st = df['isST'].astype(int) if 'isST' in df.columns else 0
                # 根据板块确定涨跌幅限制
                if sym.startswith('3') or sym.startswith('68'):
                    _limit_pct = 0.20   # 创业板/科创板 ±20%
                else:
                    _limit_pct = 0.10   # 普通主板 ±10%
                # ST 股票缩小到 ±5%（如果有 isST 列）
                if 'isST' in df.columns:
                    _limit_series = np.where(df['isST'].astype(int) == 1, 0.05, _limit_pct)
                else:
                    _limit_series = _limit_pct
                df['_high_limit'] = np.round(_pre_close * (1 + _limit_series), 2)
                df['_low_limit'] = np.round(_pre_close * (1 - _limit_series), 2)
                df['_pre_close'] = _pre_close
                # 判断昨日收盘是否涨停封板（close == high_limit）
                df['_is_limit_up'] = (np.abs(c - df['_high_limit']) < 0.005).astype(int)
                df['_is_limit_down'] = (np.abs(c - df['_low_limit']) < 0.005).astype(int)
                # 填 NaN
                _fill_cols = ['_m5','_m20','_m60','_ma20d','_ma60d','_avg_tr','_vol20','_vol60',
                              '_disp','_f_lu','_surge','_mdiv',
                              '_high_limit','_low_limit','_pre_close','_is_limit_up','_is_limit_down']
                df[_fill_cols] = df[_fill_cols].fillna(0)

                # 计算 TTM ROE 和 TTM ROA
                # pb: 总市值 / 净资产
                # pe: 这里实际上是 pe_ttm (见 data/fetcher.py df['pe'] = bs['peTTM']) = 总市值 / 净利润_TTM
                # TTM_ROE = pb / pe_ttm = (总市值 / 净资产) / (总市值 / 净利润_TTM) = 净利润_TTM / 净资产
                df['_ttm_roe'] = np.nan
                df['_ttm_roa'] = np.nan
                if 'pe' in df.columns and 'pb' in df.columns:
                    # 避免对 0 除引发无穷大
                    valid_mask = (df['pe'] > 0)
                    # 计算出的 ttm_roe 已经是比率形式（净利润/净资产），这里乘以 100 以匹配使用中的百分比格式（例如 15 表示 15%）
                    df.loc[valid_mask, '_ttm_roe'] = df.loc[valid_mask, 'pb'] / df.loc[valid_mask, 'pe'] * 100

                    # TTM ROA = TTM ROE / 权益乘数
                    eq_mul = self.equity_multiplier_dict.get(sym)
                    if eq_mul and eq_mul > 0:
                        df['_ttm_roa'] = df['_ttm_roe'] / eq_mul

                # ========= 东财无未来函数 ROA 融合 =========
                if hasattr(self, '_em_roa_dict') and sym in self._em_roa_dict:
                    em_roa_df = self._em_roa_dict[sym]
                    # 使用 merge_asof 根据 '日期' <= 'pub_date' 就近合并前面的最新财报
                    merged = pd.merge_asof(
                        df[['日期']],
                        em_roa_df[['pub_date', 'roa', 'roe']],
                        left_on='日期',
                        right_on='pub_date'
                    )
                    # roa/roe 在东财库里的如果是百分比直接沿用（例如 15 代表 15%），如果是小数（0.15）需要看 db
                    # 注意：fetch_roa.py 中入库的是东财原值百分比形式！(比如 10.5 就是 10.5%)
                    df['_east_roa'] = merged['roa'].values
                    df['_east_roe'] = merged['roe'].values
                else:
                    df['_east_roa'] = np.nan
                    df['_east_roe'] = np.nan

                # 建立日期字符串索引（原地操作，避免 copy 开销）
                dk = df['日期'].dt.strftime('%Y-%m-%d').values
                idx_df = df.set_index(pd.Index(dk, name='_dk'))
                self._indexed_data[sym] = idx_df
                count += 1
            except Exception as e:
                if count < 3: logger.warning(f"因子预计算失败 {sym}: {e}")
            if count % 500 == 0 and count > 0:
                logger.info(f"因子预计算进度: {count}/{len(price_data)}")
        logger.info(f"因子预计算完成: {count} 只, 耗时 {_t.time()-t0:.1f}s")

    def run(
        self,
        strategy,
        price_data: dict[str, pd.DataFrame],
        stock_num: int = 10,
        index_name: str = "",
        stop_event=None,
        start_date: str = None,
    ) -> PortfolioBacktestResult:
        """
        运行组合回测

        Args:
            strategy: PortfolioStrategy 实例
            price_data: 股票日线数据字典
            index_name: 指数名称
            start_date: 可选，如 '2020-01-01'，在此之前的交易日仅用于因子预热计算，不做实际调仓
        Returns:
            PortfolioBacktestResult
        """
        if not price_data:
            logger.error("价格数据为空，无法回测")
            return self._empty_result(strategy.name, index_name)

        # 统一日期轴，取所有股票的交易日并集
        all_dates = None
        for sym, df in price_data.items():
            dates = set(df['日期'].dt.date)
            if all_dates is None:
                all_dates = dates
            else:
                all_dates = all_dates | dates

        if not all_dates:
            logger.error("股票之间没有共同交易日")
            return self._empty_result(strategy.name, index_name)

        trading_dates = filter_a_share_trading_dates(sorted([pd.Timestamp(d) for d in all_dates]))
        # 【时间切片机制】：如果有指定 start_date，则在循环数组中直接过滤掉 start_date 之前的天数
        if start_date:
            target_start = pd.Timestamp(start_date)
            trading_dates = [d for d in trading_dates if d >= target_start]

        if not trading_dates:
            logger.error("过滤非交易日后没有可回测日期")
            return self._empty_result(strategy.name, index_name)

        logger.info(f"回测区间: {trading_dates[0].date()} ~ {trading_dates[-1].date()}, "
                    f"{len(trading_dates)} 个交易日, {len(price_data)} 只股票")

        # 预计算技术指标（EMA/MACD/RSI/BB/ADX等），五维共振等策略依赖
        # 性能优化：只有在策略显式声明需要 tech_indicators 时才计算，否则直接跳过，节省 50% 起步耗时！
        if getattr(strategy, 'needs_tech_indicators', False):
            self._enrich_with_tech_indicators(price_data, stop_event)
        else:
            logger.info(f"[{strategy.name}] 策略无需繁重技术指标组合，主动绕过 _enrich_with_tech_indicators 提速！")

        # 预计算所有因子并建立日期索引查找表（核心性能优化）
        self._precompute_factors(price_data, stop_event)

        # ===== 策略级参数覆盖（允许策略精确控制止损/滑点等引擎行为） =====
        _orig_stop_loss = self.stop_loss_pct
        _orig_slippage = self.slippage
        if hasattr(strategy, 'stop_loss_pct'):
            self.stop_loss_pct = strategy.stop_loss_pct
            logger.info(f"[{strategy.name}] 策略覆盖止损参数: {self.stop_loss_pct}")
        if hasattr(strategy, 'slippage_override'):
            self.slippage = strategy.slippage_override
            logger.info(f"[{strategy.name}] 策略覆盖滑点参数: {self.slippage}")

        # 检查是否需要日内高频辅助数据 (baostock 14:00 查价)
        bs_enabled = hasattr(strategy, 'check_intraday_limit_up') and getattr(strategy, 'check_intraday_limit_up')
        if bs_enabled:
            import baostock as bs
            bs.login()

        def _get_1400_price(sym: str, date_str: str) -> float:
            """带断连重试的 baostock 14:00 查价"""
            import time as _t_mod
            bs_sym = "sh." + sym if sym.startswith('6') else "sz." + sym
            for attempt in range(3):
                try:
                    _t_mod.sleep(0.05)  # 限流 50ms
                    rs = bs.query_history_k_data_plus(bs_sym, "time,close", start_date=date_str, end_date=date_str, frequency="30", adjustflag="2")
                    if not rs: return 0.0
                    while rs.error_code == '0' and rs.next():
                        t, c = rs.get_row_data()
                        if t.endswith('140000000'):
                            return float(c)
                    return 0.0
                except Exception as e:
                    logger.warning(f"baostock 查价失败 {sym} (第{attempt+1}次): {e}，重连...")
                    try:
                        bs.logout()
                    except: pass
                    try:
                        bs.login()
                    except: pass
            return 0.0

        # 为每只股票建立日期索引的价格查找表
        price_lookup: dict[str, dict[str, float]] = {}
        open_price_lookup: dict[str, dict[str, float]] = {}
        for sym, df in price_data.items():
            # 性能优化：向量化构建替代逐行 iterrows（提速 60x）
            dates_arr = df['日期'].dt.strftime('%Y-%m-%d').values
            close_arr = df['收盘'].values
            open_arr = df['开盘'].values
            price_lookup[sym] = dict(zip(dates_arr, close_arr))
            open_price_lookup[sym] = dict(zip(dates_arr, open_arr))

        # 获取调仓日期
        rebalance_dates = set(
            str(d.date()) for d in strategy.get_rebalance_dates(trading_dates)
        )
        logger.info(f"调仓日: {len(rebalance_dates)} 个")

        # ===== 模拟开始 =====
        cash = self.initial_capital
        holdings: dict[str, dict] = {}  # {symbol: {"shares", "cost_price"}}
        nav_series = []
        rebalance_records = []
        holding_counts = []
        total_trade_cost = 0.0

        for day_idx, date in enumerate(trading_dates):
            # 每个交易日检查中断信号（is_set() 是 O(1) 操作，开销可忽略）
            if stop_event and stop_event.is_set():
                logger.info(f"回测引擎收到中断信号，在第 {day_idx} 个交易日停止")
                break
            date_key = str(date.date())

            # 计算当日组合市值
            portfolio_value = cash
            for sym, h in holdings.items():
                price = price_lookup.get(sym, {}).get(date_key)
                if price:
                    portfolio_value += h['shares'] * price

            # ============================================================
            # 【模拟聚宽神仙函数 check_limit_up】日内高频 14:00 炸板秒平仓
            # 逻辑：对于昨日涨停的持仓，如果在今天 14:00 的价格跌破了今日涨停价（炸板）
            # 则立刻在 14:00 以当前价秒卖出！这是量化引擎降维打击微盘股坑人的核心
            # ============================================================
            if bs_enabled and holdings:
                to_intraday_sell = []
                for sym, h in list(holdings.items()):
                    if h.get('was_limit_up_yesterday', False):
                        p1400 = _get_1400_price(sym, date_key)
                        # 查询该股今日最高涨停价限制
                        idf = self._indexed_data.get(sym)
                        high_limit_today = 0.0
                        if idf is not None and date_key in idf.index:
                            r = idf.loc[date_key]
                            high_limit_today = r.to_dict().get('_high_limit', 0.0) if hasattr(r, 'to_dict') else 0.0

                        if p1400 > 0 and high_limit_today > 0 and p1400 < high_limit_today:
                            logger.info(f"[{sym}] ⚠️ 日内炸板拦截！14:00 价格({p1400}) < 涨停门槛({high_limit_today})，执行强平行李")
                            to_intraday_sell.append((sym, p1400))

                if to_intraday_sell:
                    il_record = RebalanceRecord(date=date)
                    for sym, sale_price in to_intraday_sell:
                        h = holdings[sym]
                        sell_price = sale_price * (1 - self.slippage)
                        revenue = h['shares'] * sell_price
                        sell_cost = revenue * (self.commission + self.stamp_tax)
                        cash += revenue - sell_cost

                        il_record.sells.append({
                            "symbol": sym, "shares": h['shares'],
                            "price": round(sell_price, 2), "cost": round(sell_cost, 2),
                        })
                        il_record.total_cost += sell_cost
                        holdings.pop(sym)

                    if il_record.sells:
                        total_trade_cost += il_record.total_cost
                        rebalance_records.append(il_record)

                        # 重新核查组合市值
                        portfolio_value = cash
                        for sym, h in holdings.items():
                            p = price_lookup.get(sym, {}).get(date_key)
                            if p: portfolio_value += h['shares'] * p

            # 执行刚性止损检查（日级别）
            if self.stop_loss_pct is not None:
                to_stop_loss = []
                # 注意：转为 list 防止字典遍历中删除元素
                for sym, h in list(holdings.items()):
                    price = price_lookup.get(sym, {}).get(date_key)
                    if price and h['cost_price'] > 0:
                        pnl_pct = (price - h['cost_price']) / h['cost_price']
                        if pnl_pct <= self.stop_loss_pct:
                            to_stop_loss.append((sym, price))

                if to_stop_loss:
                    sl_record = RebalanceRecord(date=date)
                    for sym, price in to_stop_loss:
                        h = holdings[sym]
                        sell_price = price * (1 - self.slippage)
                        revenue = h['shares'] * sell_price
                        sell_cost = revenue * (self.commission + self.stamp_tax)
                        cash += revenue - sell_cost

                        sl_record.sells.append({
                            "symbol": sym, "shares": h['shares'],
                            "price": round(sell_price, 2), "cost": round(sell_cost, 2),
                        })
                        sl_record.total_cost += sell_cost
                        holdings.pop(sym)

                    if sl_record.sells:
                        total_trade_cost += sl_record.total_cost
                        rebalance_records.append(sl_record)

                        # 重算市值（因为止损产生了成本损耗且套现）
                        portfolio_value = cash
                        for sym, h in holdings.items():
                            price = price_lookup.get(sym, {}).get(date_key)
                            if price:
                                portfolio_value += h['shares'] * price

            # 是否为调仓日
            if date_key in rebalance_dates:
                # 构建截面数据：从预计算的日期索引 DataFrame 直接查找（O(1) 每只股票）
                universe_rows = []
                for sym in price_data.keys():
                    price = price_lookup.get(sym, {}).get(date_key)
                    if not price or price <= 0:
                        continue

                    idf = self._indexed_data.get(sym)
                    if idf is None or date_key not in idf.index:
                        continue

                    try:
                        r = idf.loc[date_key]
                    except Exception:
                        continue

                    # 性能优化：将 pandas Series 转为纯 Python dict，消灭 74 万次 pandas 随机寻址开销
                    rd = r.to_dict() if hasattr(r, 'to_dict') else {}

                    # 计算精确的上市天数
                    list_days = 9999
                    ipo_date = self.ipo_dict.get(sym)
                    if ipo_date:
                        list_days = (date - ipo_date).days

                    def _g(col, default=0):
                        v = rd.get(col, default)
                        if v is None: return default
                        try:
                            fv = float(v)
                            return fv if fv == fv else default  # NaN check: NaN != NaN
                        except (ValueError, TypeError):
                            return default

                    # 注入今天的 9:30 集合竞价开盘价和限制，以还原原版动态过滤机制
                    today_open = float('nan')
                    today_high_limit = float('nan')
                    today_low_limit = float('nan')
                    today_paused = False
                    idf = self._indexed_data.get(sym)
                    if idf is not None and date_key in idf.index:
                        tr = idf.loc[date_key]
                        today_open = float(tr.get('开盘', float('nan')))
                        today_high_limit = float(tr.get('_high_limit', float('nan')))
                        today_low_limit = float(tr.get('_low_limit', float('nan')))
                        # 成交量判断是否停牌
                        today_vol = float(tr.get('成交量', 0))
                        if today_vol <= 0 or (tr.get('开盘', 0) == tr.get('收盘', 0) and today_vol == 0):
                            today_paused = True

                    row = {
                        'symbol': sym, 'close': price, 'list_days': list_days,
                        'open': _g('开盘', price),
                        'name': self.name_dict.get(sym, str(rd.get('name', sym))),
                        'market_cap': max(_g('market_cap'), price),
                        'industry': self.industry_dict.get(sym, str(rd.get('industry', '未知'))),
                        'pe': _g('pe'), 'pb': _g('pb'), 'isST': int(_g('isST')),
                        'change_pct': _g('_m20'), 'momentum_5d': _g('_m5'),
                        'momentum_20d': _g('_m20'), 'momentum_60d': _g('_m60'),
                        'ma20_deviation': _g('_ma20d'), 'ma60_deviation': _g('_ma60d'),
                        'turnover': _g('_avg_tr'), 'volatility': _g('_vol20'),
                        'volatility_60d': _g('_vol60'),
                        'disposition_ratio': _g('_disp'), 'failed_limit_up': int(_g('_f_lu')),
                        'turnover_surge_in_decline': _g('_surge'),
                        'momentum_diverge': _g('_mdiv'),
                        # 涨跌停相关字段（昨日数据）
                        'high_limit': _g('_high_limit'),
                        'low_limit': _g('_low_limit'),
                        # 今日 9:30 数据（提供给高级策略用于对齐高频交易门槛）
                        'today_open': today_open,
                        'today_high_limit': today_high_limit,
                        'today_low_limit': today_low_limit,
                        'today_paused': today_paused,
                        'pre_close': _g('_pre_close'),
                        'is_limit_up': int(_g('_is_limit_up')),
                        'is_limit_down': int(_g('_is_limit_down')),
                        # 原版 ROE（当没有_east_roe时才回落到极端的baostock最新数据兜底）
                        # 注意：东财直接入库是百分比(15.0)，baostock是小数(0.15)，为了兼顾所有老策略，东财roe除以100
                        'roe': _g('_east_roe')/100.0 if not np.isnan(_g('_east_roe')) else self.roe_dict.get(sym, float('nan')),
                        'roa': _g('_east_roa'), # ROA 保持百分比，给搅屎棍专用
                        # 基于行情数据推导的高频纯 TTF 因子（百分比形式）
                        'ttm_roe': _g('_ttm_roe', float('nan')),
                        'ttm_roa': _g('_ttm_roa', float('nan')),
                        # 申万一级行业分类（精确对齐聚宽 SW1）
                        # 申万一级行业分类（精确对齐聚宽 SW1）
                        'sw1_code': self.sw1_dict.get(sym, {}).get('sw1_code', ''),
                        'sw1_name': self.sw1_dict.get(sym, {}).get('sw1_name', ''),
                    }
                    if len(universe_rows) == 0:
                        logger.debug(f"[DEBUG] sym={sym}, dict_has={sym in self.industry_dict}, mapped={self.industry_dict.get(sym)}, final_row={row['industry']}")
                    # 技术指标
                    for col in ['ema5','ema10','ema20','adx','macd_hist','macd_hist_slope',
                                'rsi','bb_pct_b','atr_breakout','atr_compress',
                                'vol_ratio','vol_ratio_20','obv_slope','is_20d_high','ema_golden_cross']:
                        row[col] = _g(col)
                    universe_rows.append(row)

                if universe_rows:
                    universe_df = pd.DataFrame(universe_rows)
                    # 防御：去除重复 symbol（保留最后一条，通常是更新的数据）
                    universe_df = universe_df.drop_duplicates(subset=['symbol'], keep='last')
                    universe_df = universe_df.set_index('symbol')

                    # 获取目标权重（传入当前持仓，供策略做涨跌停豁免判断）
                    current_holding_syms = set(holdings.keys())
                    try:
                        target_weights = strategy.get_target_weights(date, universe_df, current_holdings=current_holding_syms)
                    except TypeError:
                        # 兼容未升级签名的旧策略
                        target_weights = strategy.get_target_weights(date, universe_df)

                    # === [临时禁用] 可插拔因子后处理钩子 — AB 排查中 ===
                    # timing_factors = getattr(strategy, 'timing_factors', None)
                    # if timing_factors and target_weights:
                    #     for factor in timing_factors:
                    #         target_weights = factor.apply(date_key, target_weights)

                    if target_weights:
                        is_inc = getattr(strategy, 'incremental_rebalance', False)
                        trade_at_open = getattr(strategy, 'trade_at_open', False)
                        record = self._rebalance(
                            date, cash, holdings, target_weights,
                            price_lookup, date_key, portfolio_value, is_inc, open_price_lookup, trade_at_open,
                            max_holdings=strategy.rebalance_config.max_holdings
                        )
                        cash = record['cash']
                        holdings = record['holdings']
                        total_trade_cost += record['rebalance_record'].total_cost
                        rebalance_records.append(record['rebalance_record'])

                        # 重新计算组合市值
                        portfolio_value = cash
                        for sym, h in holdings.items():
                            price = price_lookup.get(sym, {}).get(date_key)
                            if price:
                                portfolio_value += h['shares'] * price

            # === 记录今日行情给明天 14:00 高频炸板判定用 ===
            if bs_enabled:
                for sym, h in holdings.items():
                    try:
                        rdf = self._indexed_data.get(sym)
                        if rdf is not None and date_key in rdf.index:
                            r = rdf.loc[date_key]
                            is_lu = r.to_dict().get('_is_limit_up', 0) if hasattr(r, 'to_dict') else 0
                            h['was_limit_up_yesterday'] = (int(is_lu) == 1)
                    except Exception:
                        h['was_limit_up_yesterday'] = False

            nav = portfolio_value / self.initial_capital
            nav_series.append({"date": date_key, "nav": round(nav, 4)})
            holding_counts.append(len(holdings))

        # ===== 计算统计 =====
        result = PortfolioBacktestResult(
            strategy_name=strategy.name,
            index_name=index_name,
            start_date=trading_dates[0],
            end_date=trading_dates[-1],
            initial_capital=self.initial_capital,
        )

        navs = [p['nav'] for p in nav_series]
        result.final_nav = navs[-1] if navs else 1.0
        result.total_return_pct = (result.final_nav - 1) * 100
        result.nav_series = nav_series

        days = (trading_dates[-1] - trading_dates[0]).days
        if days > 0:
            result.annual_return_pct = (result.final_nav ** (365 / days) - 1) * 100

        # 最大回撤
        nav_array = np.array(navs)
        peak = np.maximum.accumulate(nav_array)
        drawdown = (nav_array - peak) / peak * 100
        result.max_drawdown_pct = drawdown.min()

        # 夏普比率和波动率
        returns = pd.Series(navs).pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            result.sharpe_ratio = (returns.mean() * 252 - 0.03) / (returns.std() * np.sqrt(252))
            result.volatility_pct = returns.std() * np.sqrt(252) * 100

        # 组合统计
        result.total_rebalances = len(rebalance_records)
        result.total_trade_cost = total_trade_cost
        result.avg_holdings = np.mean(holding_counts) if holding_counts else 0

        turnovers = [r.turnover_pct for r in rebalance_records if r.turnover_pct > 0]
        result.avg_turnover_pct = np.mean(turnovers) if turnovers else 0

        result.rebalance_records = rebalance_records

        # 最终持仓
        last_date_key = str(trading_dates[-1].date())
        for sym, h in holdings.items():
            price = price_lookup.get(sym, {}).get(last_date_key, h['cost_price'])
            mv = h['shares'] * price
            final_value = cash + sum(
                hh['shares'] * price_lookup.get(s, {}).get(last_date_key, hh['cost_price'])
                for s, hh in holdings.items()
            )
            result.final_holdings.append(HoldingSnapshot(
                symbol=sym,
                name=sym,
                shares=h['shares'],
                cost_price=h['cost_price'],
                current_price=price,
                market_value=mv,
                weight=mv / final_value if final_value > 0 else 0,
                pnl_pct=(price - h['cost_price']) / h['cost_price'] * 100 if h['cost_price'] > 0 else 0,
            ))

        logger.info(f"组合回测完成: {strategy.name} | "
                    f"收益 {result.total_return_pct:+.2f}% | "
                    f"最大回撤 {result.max_drawdown_pct:.2f}% | "
                    f"调仓 {result.total_rebalances} 次")

        # 恢复引擎原始参数（防止复用时污染其他策略）
        self.stop_loss_pct = _orig_stop_loss
        self.slippage = _orig_slippage

        return result

    def _rebalance(
        self,
        date: pd.Timestamp,
        cash: float,
        holdings: dict,
        target_weights: dict[str, float],
        price_lookup: dict,
        date_key: str,
        portfolio_value: float,
        is_incremental: bool = False,
        open_price_lookup: dict = None,
        trade_at_open: bool = False,
        max_holdings: int = 10,
    ) -> dict:
        """
        执行一次调仓（先卖后买）

        Returns:
            {"cash", "holdings", "rebalance_record"}
        """
        record = RebalanceRecord(date=date)

        trade_lookup = open_price_lookup if (trade_at_open and open_price_lookup is not None) else price_lookup

        # 当前持仓及目标的对比
        current_symbols = set(holdings.keys())
        target_symbols = set(target_weights.keys())

        # 需要卖出的：不在目标中的股票 + 需要减仓的
        to_sell = current_symbols - target_symbols
        # 需要买入的：不在持仓中的股票 + 需要加仓的
        to_buy = target_symbols - current_symbols
        # 需要调整的：既在持仓又在目标中的
        to_adjust = current_symbols & target_symbols



        if is_incremental:
            # 增量模式：过滤掉昨日涨停的持仓，免于被卖出（对齐原版）
            to_sell = {s for s in to_sell if not holdings.get(s, {}).get('was_limit_up_yesterday', False)}
            # 增量模式下，在目标里的已有持仓不作任何主动减仓调整，任由其漂移
            to_adjust = set()

        # ===== 第一步：全部卖出 =====
        for sym in to_sell:
            if sym not in holdings:
                continue
            h = holdings[sym]
            price = trade_lookup.get(sym, {}).get(date_key)
            if not price:
                continue

            sell_price = price * (1 - self.slippage)
            revenue = h['shares'] * sell_price
            sell_cost = revenue * (self.commission + self.stamp_tax)
            cash += revenue - sell_cost

            record.sells.append({
                "symbol": sym, "shares": h['shares'],
                "price": round(sell_price, 2), "cost": round(sell_cost, 2),
            })
            record.total_cost += sell_cost

        # 删除已卖出的
        for sym in to_sell:
            holdings.pop(sym, None)

        # 调整仓位：需要减仓的先卖
        for sym in to_adjust:
            target_value = portfolio_value * target_weights.get(sym, 0)
            price = trade_lookup.get(sym, {}).get(date_key)
            if not price or price <= 0:
                continue

            target_shares = int(target_value / price / 100) * 100
            current_shares = holdings[sym]['shares']

            if target_shares < current_shares:
                # 部分卖出
                sell_shares = current_shares - target_shares
                sell_price = price * (1 - self.slippage)
                revenue = sell_shares * sell_price
                sell_cost = revenue * (self.commission + self.stamp_tax)
                cash += revenue - sell_cost

                holdings[sym]['shares'] = target_shares
                record.sells.append({
                    "symbol": sym, "shares": sell_shares,
                    "price": round(sell_price, 2), "cost": round(sell_cost, 2),
                })
                record.total_cost += sell_cost

                if target_shares == 0:
                    holdings.pop(sym)

        # ===== 第二步：买入 =====
        # 计算买入需要的总金额，确保不超过可用现金
        buy_targets = []

        if is_incremental:
            # ⚠️ 原封不动复刻原版（jsg.py）的增量挂单Bug逻辑（允许闲置资金防守）
            target_num = len(target_weights)
            position_count = len(holdings)

            if target_num > position_count:
                buy_num = min(target_num, max_holdings - position_count)
                if buy_num > 0:
                    incremental_value_per_stock = cash / buy_num
                    for sym in to_buy:
                        price = trade_lookup.get(sym, {}).get(date_key)
                        if not price or price <= 0: continue
                        target_shares = int(incremental_value_per_stock / price / 100) * 100
                        if target_shares > 0:
                            buy_targets.append((sym, target_shares, price))
            else:
                # 聚宽原版Bug 1：如果选出标的少于已有持仓数，什么都不买，完全空跑！
                logger.info(f"    [原版联动] 目标数({target_num}) <= 持仓数({position_count})，触发原版Bug空跑跳过买入，持有现金")
        else:
            # 标准再平衡模式：强行根据 target_weights 分配每一只的目标市值
            for sym in to_buy | {s for s in to_adjust if s in holdings}:
                target_value = portfolio_value * target_weights.get(sym, 0)
                price = trade_lookup.get(sym, {}).get(date_key)
                if not price or price <= 0:
                    continue

                target_shares = int(target_value / price / 100) * 100
                current_shares = holdings.get(sym, {}).get('shares', 0)

                if target_shares > current_shares:
                    buy_shares = target_shares - current_shares
                    buy_targets.append((sym, buy_shares, price))

        # 按需要资金从小到大排序买入
        buy_targets.sort(key=lambda x: x[1] * x[2])

        for sym, buy_shares, price in buy_targets:
            buy_price = price * (1 + self.slippage)
            total_cost = buy_shares * buy_price * (1 + self.commission)

            if total_cost > cash:
                # 资金不够，减少买入量
                affordable_shares = int(cash / (buy_price * (1 + self.commission)) / 100) * 100
                if affordable_shares <= 0:
                    continue
                buy_shares = affordable_shares
                total_cost = buy_shares * buy_price * (1 + self.commission)

            cash -= total_cost
            buy_cost = buy_shares * buy_price * self.commission
            record.total_cost += buy_cost

            if sym in holdings:
                # 加仓：更新平均成本
                old = holdings[sym]
                total_shares = old['shares'] + buy_shares
                avg_cost = (old['cost_price'] * old['shares'] + buy_price * buy_shares) / total_shares
                holdings[sym] = {'shares': total_shares, 'cost_price': avg_cost}
            else:
                holdings[sym] = {'shares': buy_shares, 'cost_price': buy_price}

            record.buys.append({
                "symbol": sym, "shares": buy_shares,
                "price": round(buy_price, 2), "cost": round(buy_cost, 2),
            })

        # 换手率
        sell_value = sum(s['shares'] * s['price'] for s in record.sells)
        buy_value = sum(b['shares'] * b['price'] for b in record.buys)
        if portfolio_value > 0:
            record.turnover_pct = (sell_value + buy_value) / 2 / portfolio_value * 100

        return {
            "cash": cash,
            "holdings": holdings,
            "rebalance_record": record,
        }

    def _empty_result(self, strategy_name: str, index_name: str) -> PortfolioBacktestResult:
        return PortfolioBacktestResult(
            strategy_name=strategy_name,
            index_name=index_name,
            start_date=pd.Timestamp.now(),
            end_date=pd.Timestamp.now(),
            initial_capital=self.initial_capital,
            final_nav=1.0,
        )
