"""
共享市场数据缓存 — 盘前预处理 + 盘中快照

多 Worker（V1/V2/...）共享同一份数据，避免：
1. 重复查询 daily_kline 计算 20 日阈值（每日仅首次调用者执行，后续直接返回缓存）
2. 重复调用 Sina API 拉取全 A 快照（TTL 内共享同一份快照）

线程安全：所有方法通过 Lock 保护，支持多 Worker 并发调用。
"""

import sys
import io
import time
import threading
from datetime import datetime
from loguru import logger
import pandas as pd
import numpy as np


class SharedMarketData:
    """线程安全的市场数据共享单例"""

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # ===== 盘前预处理缓存 =====
        self.thresholds: dict = {}
        self.last_precompute_date: str = ''
        self._precompute_lock = threading.Lock()

        # ===== 快照缓存 =====
        self._snapshot: pd.DataFrame | None = None
        self._snapshot_ts: float = 0
        self._snapshot_lock = threading.Lock()
        self.SNAPSHOT_TTL = 45  # 秒（小于扫描间隔 60s，保证同周期内共享）

    # ------------------------------------------------------------------
    #  盘前预处理（每日仅执行一次）
    # ------------------------------------------------------------------
    def precompute_thresholds(
        self,
        vol_threshold: float = 3.0,
        min_list_days: int = 60,
        min_price: float = 3.0,
        min_circ_mv: float = 20e8,
        max_circ_mv: float = 500e8,
        caller: str = '',
    ) -> dict:
        """
        计算全 A 股的 20 日阈值。

        多个 Worker 并发调用时，仅首个到达的线程执行实际计算，
        后续调用直接返回缓存。每日只执行一次（按日期去重）。

        返回 thresholds dict（键=symbol，值=阈值字典）。
        """
        today_str = datetime.now().strftime('%Y-%m-%d')

        # 快速路径：已缓存
        if self.last_precompute_date == today_str and self.thresholds:
            logger.info(f'[共享缓存] {caller} 命中盘前预处理缓存 ({len(self.thresholds)} 只)')
            return self.thresholds

        with self._precompute_lock:
            # 双重检查（另一个线程可能刚算完）
            if self.last_precompute_date == today_str and self.thresholds:
                logger.info(f'[共享缓存] {caller} 命中盘前预处理缓存 ({len(self.thresholds)} 只)')
                return self.thresholds

            logger.info(f'[共享缓存] {caller} 执行盘前预处理...')
            result = self._do_precompute(
                vol_threshold, min_list_days, min_price, min_circ_mv, max_circ_mv
            )
            if result:
                self.last_precompute_date = today_str
                logger.info(f'[共享缓存] 盘前预处理完成: {len(self.thresholds)} 只, 日期: {today_str}')
            return self.thresholds

    def _do_precompute(
        self,
        vol_threshold: float,
        min_list_days: int,
        min_price: float,
        min_circ_mv: float,
        max_circ_mv: float,
    ) -> bool:
        """实际执行盘前预处理（内部方法，已持有锁）"""
        from data.database import DatabaseManager

        db = DatabaseManager()
        conn = db._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT date FROM daily_kline
                ORDER BY date DESC LIMIT 25
            """)
            dates = [r[0] for r in cursor.fetchall()]
            if len(dates) < 20:
                logger.warning(f'[共享缓存] 历史数据不足: {len(dates)} 天')
                return False

            min_date = dates[-1]

            cursor.execute("""
                SELECT symbol, date, close, high, low, volume, is_st, circ_mv
                FROM daily_kline
                WHERE date >= %s
                ORDER BY symbol, date
            """, (min_date,))
            rows = cursor.fetchall()

            if not rows:
                logger.warning('[共享缓存] 未获取到任何历史行情数据')
                return False

            df = pd.DataFrame(rows, columns=['symbol', 'date', 'close', 'high', 'low', 'volume', 'is_st', 'circ_mv'])

            # 排除 ST
            df = df[df['is_st'] == 0]

            # 排除次新股
            cursor.execute("SELECT symbol, ipo_date FROM stock_basic WHERE ipo_date IS NOT NULL")
            ipo_rows = cursor.fetchall()
            if ipo_rows:
                ipo_df = pd.DataFrame(ipo_rows, columns=['symbol', 'ipo_date'])
                today_str = datetime.now().strftime('%Y-%m-%d')
                ipo_df['days_listed'] = (pd.to_datetime(today_str) - pd.to_datetime(ipo_df['ipo_date'])).dt.days
                new_stocks = set(ipo_df[ipo_df['days_listed'] < min_list_days]['symbol'].tolist())
                df = df[~df['symbol'].isin(new_stocks)]

            # 计算每只股票的阈值
            new_thresholds = {}
            for sym, grp in df.groupby('symbol'):
                grp = grp.sort_values('date')
                if len(grp) < 20:
                    continue

                closes = grp['close'].values
                highs = grp['high'].values
                lows = grp['low'].values
                volumes = grp['volume'].values

                last_close = float(closes[-1])
                last_volume = float(volumes[-1])

                if last_close < min_price:
                    continue

                latest_circ_mv = grp['circ_mv'].values[-1]
                if latest_circ_mv is not None and latest_circ_mv > 0:
                    if latest_circ_mv < min_circ_mv or latest_circ_mv > max_circ_mv:
                        continue

                high_20d = float(np.max(closes[-20:]))
                avg_vol_20d = float(np.mean(volumes[-20:]))

                # ATR(20): True Range 的 20 日均值
                # TR = max(H-L, |H-prevC|, |L-prevC|)
                tr_values = []
                for i in range(1, min(21, len(grp))):
                    h = float(highs[-i])
                    l = float(lows[-i])
                    prev_c = float(closes[-i-1]) if i < len(grp) else h
                    tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                    tr_values.append(tr)
                atr_20d = float(np.mean(tr_values)) if tr_values else 0.0

                new_thresholds[str(sym)] = {
                    'high_20d': high_20d,
                    'vol_burst': avg_vol_20d * vol_threshold,
                    'avg_vol_20d': avg_vol_20d,
                    'pre_close': last_close,
                    'yest_volume': last_volume,
                    'circ_mv': float(latest_circ_mv) if latest_circ_mv and latest_circ_mv > 0 else 0,
                    'atr_20d': atr_20d,
                }

            self.thresholds = new_thresholds
            return True

        except Exception as e:
            logger.error(f'[共享缓存] 盘前预处理失败: {e}')
            return False
        finally:
            conn.close()

    # ------------------------------------------------------------------
    #  快照缓存（TTL 内共享）
    # ------------------------------------------------------------------
    def fetch_snapshot(self, caller: str = '') -> pd.DataFrame | None:
        """
        获取全 A 股 Sina 快照。

        TTL 内返回缓存版本，避免多 Worker 重复请求 Sina API。
        """
        now = time.time()

        # 快速路径：缓存有效
        if self._snapshot is not None and (now - self._snapshot_ts) < self.SNAPSHOT_TTL:
            return self._snapshot

        with self._snapshot_lock:
            # 双重检查
            now = time.time()
            if self._snapshot is not None and (now - self._snapshot_ts) < self.SNAPSHOT_TTL:
                return self._snapshot

            snap = self._do_fetch_snapshot()
            if snap is not None:
                self._snapshot = snap
                self._snapshot_ts = time.time()
                logger.debug(f'[共享缓存] 快照已刷新 ({len(snap)} 行), 请求者: {caller}')
            return snap

    def _do_fetch_snapshot(self) -> pd.DataFrame | None:
        """实际执行 Sina 快照拉取"""
        import akshare as ak

        original_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            snapshot = ak.stock_zh_a_spot()
        except Exception as e:
            logger.warning(f'[共享缓存] Sina 快照拉取异常，本轮跳过: {e}')
            return None
        finally:
            sys.stderr = original_stderr

        if snapshot is None or snapshot.empty:
            return None

        snapshot['代码'] = snapshot['代码'].astype(str).str.replace(r'^[a-zA-Z]+', '', regex=True)

        snap = snapshot.rename(columns={
            '代码': 'symbol',
            '名称': 'name',
            '最新价': 'price',
            '涨跌幅': 'pct_chg',
            '成交量': 'volume',
            '今开': 'today_open',
            '昨收': 'pre_close',
            '最高': 'high',
            '最低': 'low',
            '成交额': 'amount',
        })

        snap = snap.dropna(subset=['symbol', 'price', 'volume'])
        return snap

    def invalidate_precompute(self):
        """强制下次预处理重新计算（用于测试或手动刷新）"""
        with self._precompute_lock:
            self.last_precompute_date = ''
            self.thresholds = {}
            logger.info('[共享缓存] 盘前预处理缓存已清除')


# 全局唯一实例
shared_market_data = SharedMarketData()
