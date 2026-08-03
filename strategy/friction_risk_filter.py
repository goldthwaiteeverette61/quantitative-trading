import pandas as pd
from loguru import logger

from strategy.portfolio_base import PortfolioStrategy, RebalanceConfig

class FrictionRiskFilterStrategy(PortfolioStrategy):
    """
    高频摩擦黑名单风控策略

    作为一个独立策略在UI测试展示，实战中也可以作为通用底层过滤器。
    本策略逻辑：按照一定流动性指标或截面市值选取股票，但强制拉黑 Amihud、 Turnover Vol 和 Idio Vol 处于最差 10% (G10) 区域的标的。
    """

    def __init__(
        self,
        top_n: int = 30,
        rebalance_freq: str = "monthly",
    ):
        super().__init__(RebalanceConfig(
            frequency=rebalance_freq,
            max_holdings=top_n,
        ))
        self.top_n = top_n

    @property
    def name(self) -> str:
        return "高频摩擦风控"

    @property
    def category(self) -> str:
        return "风控前置"

    @property
    def description(self) -> str:
        return "一票否决制：剔除全市场Idio Vol/换手波/Amihud垫底的垃圾股"

    @property
    def author(self) -> str:
        return "Antigravity"

    def get_target_weights(
        self,
        date: pd.Timestamp,
        universe_df: pd.DataFrame,
    ) -> dict[str, float]:
        df = universe_df.copy()

        # 1. 过滤基础垃圾票
        if 'close' in df.columns:
            df = df[df['close'] >= 2.0]
        if 'market_cap' in df.columns:
            df = df[df['market_cap'] > 20]

        # 2. 从数据库加载当天的 3 个摩擦因子
        date_str = date.strftime('%Y-%m-%d')
        blacklist = set()

        try:
            from data.database import DatabaseManager
            db = DatabaseManager()
            conn = db._get_conn()
            try:
                # 只查属于今天且在我们备选池里的股票
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT symbol, amihud_log, turnover_vol, idio_vol
                    FROM factor_daily
                    WHERE date = '{date_str}'
                """)
                factor_rows = cursor.fetchall()
                if factor_rows:
                    f_df = pd.DataFrame(factor_rows, columns=['symbol', 'amihud_log', 'turnover_vol', 'idio_vol']).set_index('symbol')
                    # 取 df 和 f_df 的交集
                    valid_symbols = df.index.intersection(f_df.index)
                    f_df = f_df.loc[valid_symbols]

                    if not f_df.empty:
                        # 3. 找出最差的 10% (G10黑名单)
                        # Amihud越大流动性越差
                        amihud_q = f_df['amihud_log'].quantile(0.90)
                        # Turnover Vol越大换手越暴躁
                        t_vol_q = f_df['turnover_vol'].quantile(0.90)
                        # Idio Vol越大特质波动越暴躁
                        i_vol_q = f_df['idio_vol'].quantile(0.90)

                        black_amihud = f_df[f_df['amihud_log'] > amihud_q].index
                        black_t_vol = f_df[f_df['turnover_vol'] > t_vol_q].index
                        black_i_vol = f_df[f_df['idio_vol'] > i_vol_q].index

                        blacklist = set(black_amihud) | set(black_t_vol) | set(black_i_vol)
                        logger.info(f"[{date_str}] 风控拉黑: Amihud({len(black_amihud)}) T_Vol({len(black_t_vol)}) I_Vol({len(black_i_vol)}), 总计去重拉黑: {len(blacklist)} 只")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"加载摩擦因子失败: {e}")

        # 4. 执行一票否决
        df = df[~df.index.isin(blacklist)]

        # 5. 排序选股：剩余池子里选市值最小的 (作为测试展示用)
        if 'market_cap' in df.columns:
            df = df.sort_values('market_cap', ascending=True)

        selected = df.head(self.top_n)

        # 6. 等权分配
        if len(selected) == 0:
            return {}

        weight = 0.95 / len(selected)
        return {sym: weight for sym in selected.index}
