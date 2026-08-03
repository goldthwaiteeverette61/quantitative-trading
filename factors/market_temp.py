"""
市场温度因子 (Market Temperature Factor) — 可插拔择时风控层

设计原则：
  - 本模块是一个独立的"滤镜"，不属于任何策略
  - 策略通过在 __init__ 中声明 self.timing_factors = [MarketTempFactor()] 来叠加
  - 引擎在调仓日执行策略的 get_target_weights 之后、_rebalance 之前调用 factor.apply()
  - 不声明 = 不生效，零侵入

信号等级：
  DEFENSIVE → 清空 target_weights（空仓）
  CAUTIOUS  → target_weights 权重缩放至 50%
  NORMAL    → 不干预，原样透传
"""
from enum import Enum
from loguru import logger


class MarketSignal(Enum):
    DEFENSIVE = "defensive"   # 空仓防守
    CAUTIOUS = "cautious"     # 半仓谨慎
    NORMAL = "normal"         # 正常操作


class MarketTempFactor:
    """
    市场温度因子

    读取 MySQL market_sentiment 表的近期数据，综合三个维度输出市场信号。

    判定规则（v1）：
      DEFENSIVE: 涨停 < 30 | 北向连续3日净流出且累计超-50亿 | 两融余额较20日均值跌超3%
      CAUTIOUS:  涨停 30~50 | 北向最近3日有2日净流出
      NORMAL:    以上均不满足
    """

    def __init__(
        self,
        zt_defensive: int = 30,
        zt_cautious: int = 50,
        north_flow_days: int = 3,
        north_flow_threshold: float = -50.0,  # 亿元
        margin_ma_window: int = 20,
        margin_drop_pct: float = 0.03,
        cautious_scale: float = 0.5,
    ):
        self.zt_defensive = zt_defensive
        self.zt_cautious = zt_cautious
        self.north_flow_days = north_flow_days
        self.north_flow_threshold = north_flow_threshold
        self.margin_ma_window = margin_ma_window
        self.margin_drop_pct = margin_drop_pct
        self.cautious_scale = cautious_scale

        # 缓存：避免同一天重复查库
        self._cache: dict[str, MarketSignal] = {}
        self._data_cache: dict[str, list] = {}

    @property
    def name(self) -> str:
        return "市场温度因子"

    def _load_recent_data(self, date_key: str, lookback: int = 25) -> list[dict]:
        """从 MySQL 加载最近 N 天的 market_sentiment 数据"""
        cache_key = f"{date_key}_{lookback}"
        if cache_key in self._data_cache:
            return self._data_cache[cache_key]

        try:
            from data.database import DatabaseManager
            db = DatabaseManager()
            conn = db._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT trade_date, zt_count, dt_count, north_net_flow, margin_balance
                    FROM market_sentiment
                    WHERE trade_date <= %s
                    ORDER BY trade_date DESC
                    LIMIT %s
                """, (date_key, lookback))
                rows = []
                for r in cursor.fetchall():
                    rows.append({
                        'trade_date': str(r[0]),
                        'zt_count': r[1],
                        'dt_count': r[2],
                        'north_net_flow': float(r[3]) if r[3] is not None else None,
                        'margin_balance': float(r[4]) if r[4] is not None else None,
                    })
                self._data_cache[cache_key] = rows
                return rows
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[市场温度因子] 加载 market_sentiment 失败: {e}")
            return []

    def get_signal(self, date_key: str) -> MarketSignal:
        """综合判定当日市场温度信号"""
        if date_key in self._cache:
            return self._cache[date_key]

        rows = self._load_recent_data(date_key, lookback=max(self.margin_ma_window + 5, 25))

        if not rows:
            logger.debug(f"[市场温度因子] {date_key} 无数据，返回 NORMAL")
            self._cache[date_key] = MarketSignal.NORMAL
            return MarketSignal.NORMAL

        signal = MarketSignal.NORMAL
        reasons = []

        # --- 维度 1: 涨停家数 ---
        today = rows[0] if rows[0]['trade_date'] == date_key else None
        if today and today['zt_count'] is not None:
            zt = today['zt_count']
            if zt < self.zt_defensive:
                signal = MarketSignal.DEFENSIVE
                reasons.append(f"涨停仅{zt}家(<{self.zt_defensive})")
            elif zt < self.zt_cautious and signal != MarketSignal.DEFENSIVE:
                signal = MarketSignal.CAUTIOUS
                reasons.append(f"涨停{zt}家(<{self.zt_cautious})")

        # --- 维度 2: 北向资金 ---
        north_recent = [r['north_net_flow'] for r in rows[:self.north_flow_days]
                        if r['north_net_flow'] is not None]
        if len(north_recent) >= self.north_flow_days:
            outflow_days = sum(1 for v in north_recent if v < 0)
            total_flow = sum(north_recent)

            if outflow_days == self.north_flow_days and total_flow < self.north_flow_threshold:
                signal = MarketSignal.DEFENSIVE
                reasons.append(f"北向连续{self.north_flow_days}日净流出，累计{total_flow:.1f}亿")
            elif outflow_days >= self.north_flow_days - 1 and signal != MarketSignal.DEFENSIVE:
                signal = MarketSignal.CAUTIOUS
                reasons.append(f"北向近{self.north_flow_days}日有{outflow_days}日净流出")

        # --- 维度 3: 两融余额 ---
        margin_values = [r['margin_balance'] for r in rows[:self.margin_ma_window + 1]
                         if r['margin_balance'] is not None]
        if len(margin_values) > self.margin_ma_window:
            current_margin = margin_values[0]
            ma_margin = sum(margin_values[1:self.margin_ma_window + 1]) / self.margin_ma_window
            if ma_margin > 0:
                drop_ratio = (current_margin - ma_margin) / ma_margin
                if drop_ratio < -self.margin_drop_pct:
                    if signal != MarketSignal.DEFENSIVE:
                        signal = MarketSignal.DEFENSIVE
                    reasons.append(f"两融余额较{self.margin_ma_window}日均值下跌{abs(drop_ratio)*100:.1f}%")

        if signal != MarketSignal.NORMAL:
            logger.info(f"[市场温度因子] {date_key} → {signal.value.upper()} | {'; '.join(reasons)}")

        self._cache[date_key] = signal
        return signal

    def apply(self, date_key: str, target_weights: dict[str, float]) -> dict[str, float]:
        """
        因子后处理钩子 — 被引擎在调仓日自动调用

        Args:
            date_key: 交易日 YYYY-MM-DD
            target_weights: 策略输出的原始目标权重

        Returns:
            经因子过滤后的目标权重（可能被缩放或清空）
        """
        if not target_weights:
            return target_weights

        signal = self.get_signal(date_key)

        if signal == MarketSignal.DEFENSIVE:
            logger.info(f"[市场温度因子] {date_key} DEFENSIVE → 清空全部 {len(target_weights)} 只持仓目标")
            return {}

        if signal == MarketSignal.CAUTIOUS:
            scaled = {k: v * self.cautious_scale for k, v in target_weights.items()}
            logger.info(f"[市场温度因子] {date_key} CAUTIOUS → 权重缩放至 {self.cautious_scale*100:.0f}%")
            return scaled

        return target_weights
