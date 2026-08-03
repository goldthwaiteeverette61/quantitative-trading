"""
数据获取引擎 v2

主数据源：东方财富 API（支持日线/周线/月线，最长 20 年历史）
备用数据源：新浪财经 API
"""
import os
import urllib.request

# 禁用代理
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['NO_PROXY'] = '*'

no_proxy_handler = urllib.request.ProxyHandler({})
opener = urllib.request.build_opener(no_proxy_handler)
urllib.request.install_opener(opener)

import requests
import pandas as pd
from typing import Optional
from pathlib import Path
from loguru import logger
import json
from datetime import datetime, timedelta

from config.settings import config
from data.database import DatabaseManager

# 无代理 session（通用）
_session = requests.Session()
_session.trust_env = False
_session.proxies = {'http': None, 'https': None}
_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Referer': 'https://finance.eastmoney.com/',
})
_session.verify = False

# 消除 SSL 警告
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 暂时取消重试策略，防止在批量请求东方财富被封禁时陷入极长(20秒+)的退避等待
# 从而实现瞬间 fallback 到稳定新浪高速通道的任务优化
_session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
_session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))


def _to_full_code(symbol: str) -> str:
    """股票代码转换为带市场前缀的格式（sz/sh）"""
    code = symbol.split('.')[0]
    market = 'sz' if code.startswith(('0', '1', '2', '3')) else 'sh'
    return f"{market}{code}"


def _to_eastmoney_secid(symbol: str) -> str:
    """
    股票代码转换为东方财富 secid 格式
    深圳: 0.000001  上海: 1.600000
    板块: 90.BK0475
    """
    code = symbol.split('.')[0]
    if code.startswith('BK'):
        return f"90.{code}"
    market = '0' if code.startswith(('0', '1', '2', '3')) else '1'
    return f"{market}.{code}"


class DataFetcher:
    """行情数据获取器（东财 + 新浪双数据源）"""

    # 东方财富 K 线类型映射
    _KLT_MAP = {
        'daily': '101',     # 日线
        'weekly': '102',    # 周线
        'monthly': '103',   # 月线
    }

    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = Path(cache_dir or config.data.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db = DatabaseManager(str(self.cache_dir))

    def _cache_path(self, symbol: str, period: str, days: int) -> Path:
        return self.cache_dir / f"{symbol}_{period}_{days}d.json"

    def _load_cache(self, symbol: str, period: str, days: int) -> Optional[pd.DataFrame]:
        cache_file = self._cache_path(symbol, period, days)
        if not cache_file.exists():
            return None

        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - mtime > timedelta(hours=config.data.cache_max_age_hours):
            return None

        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            df = pd.DataFrame(data)
            df['日期'] = pd.to_datetime(df['日期'])
            logger.debug(f"缓存命中: {symbol} {period} {len(df)}条")
            return df
        except Exception:
            return None

    def _save_cache(self, symbol: str, period: str, days: int, df: pd.DataFrame):
        cache_file = self._cache_path(symbol, period, days)
        try:
            df.to_json(cache_file, orient='records', force_ascii=False, date_format='iso')
        except Exception:
            pass

    # ===== 主数据源：东方财富 =====

    def _fetch_eastmoney(
        self, symbol: str, period: str, days: int
    ) -> pd.DataFrame:
        """
        通过东方财富 API 获取 K 线数据

        支持日线/周线/月线，前复权，最多可获取 20 年历史。
        """
        code = symbol.split('.')[0]
        secid = _to_eastmoney_secid(code)
        klt = self._KLT_MAP.get(period, '101')

        # 根据 days 计算起始日期
        end_date = datetime.now().strftime('%Y%m%d')
        if period == 'weekly':
            start_date = (datetime.now() - timedelta(days=days * 7)).strftime('%Y%m%d')
        elif period == 'monthly':
            start_date = (datetime.now() - timedelta(days=days * 30)).strftime('%Y%m%d')
        else:
            start_date = (datetime.now() - timedelta(days=int(days * 1.6))).strftime('%Y%m%d')

        url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
        params = {
            'secid': secid,
            'fields1': 'f1,f2,f3,f4,f5,f6',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
            'klt': klt,
            'fqt': '1',           # 1=前复权
            'beg': start_date,
            'end': end_date,
        }

        try:
            # 缩短超时时间到 2 秒，东财若无法瞬间连上直接放弃，触发闪电回退到新浪或 AKShare
            resp = _session.get(url, params=params, timeout=2)
            data = resp.json()

            if data.get('data') and data['data'].get('klines'):
                klines = data['data']['klines']
                rows = []
                for line in klines:
                    parts = line.split(',')
                    # 格式: 日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
                    rows.append({
                        '日期': parts[0],
                        '开盘': float(parts[1]),
                        '收盘': float(parts[2]),
                        '最高': float(parts[3]),
                        '最低': float(parts[4]),
                        '成交量': float(parts[5]),
                        '成交额': float(parts[6]),
                        '涨跌幅': float(parts[8]),
                        '换手率': float(parts[10]) if len(parts) > 10 else 0,
                    })

                df = pd.DataFrame(rows)
                df['日期'] = pd.to_datetime(df['日期'])

                # 裁剪到指定天数
                if len(df) > days:
                    df = df.tail(days).reset_index(drop=True)

                stock_name = data['data'].get('name', code)
                logger.info(
                    f"[东财] {stock_name}({code}) {period} {len(df)}条 "
                    f"({df['日期'].min().date()} ~ {df['日期'].max().date()})"
                )
                return df

            logger.warning(f"[东财] 数据为空: {code} {period}")
            return pd.DataFrame()

        except Exception as e:
            logger.error(f"[东财] 获取失败: {code} {period}, 错误: {e}")
            return pd.DataFrame()

    # ===== 备用数据源：新浪 =====

    def _fetch_sina(self, symbol: str, period: str, days: int) -> pd.DataFrame:
        """通过新浪 API 获取 K 线数据（单次上限 1023 条，约 4 年日线）"""
        code = symbol.split('.')[0]
        full_code = _to_full_code(code)
        scale = '1680' if period == 'weekly' else '240'

        # 新浪 API 上限 1023 条
        datalen = min(days, 1023)

        try:
            url = "http://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
            params = {
                'symbol': full_code,
                'scale': scale,
                'ma': 'no',
                'datalen': datalen,
            }

            resp = _session.get(url, params=params, timeout=15)
            data = resp.json()

            if data and isinstance(data, list):
                rows = []
                for item in data:
                    rows.append({
                        '日期': item['day'],
                        '开盘': float(item['open']),
                        '收盘': float(item['close']),
                        '最高': float(item['high']),
                        '最低': float(item['low']),
                        '成交量': float(item['volume']),
                    })

                df = pd.DataFrame(rows)
                df['日期'] = pd.to_datetime(df['日期'])
                df['涨跌幅'] = df['收盘'].pct_change() * 100
                df['涨跌幅'] = df['涨跌幅'].fillna(0)
                # 新浪没有换手率，补0（引擎会用历史成交量近似）
                df['换手率'] = 0.0

                logger.info(
                    f"[新浪] {code} {period} {len(df)}条 "
                    f"({df['日期'].min().date()} ~ {df['日期'].max().date()})"
                )
                return df

            return pd.DataFrame()

        except Exception as e:
            logger.error(f"[新浪] 获取失败: {code} {period}, 错误: {e}")
            return pd.DataFrame()

    def _apply_baostock_fundamentals(self, df: pd.DataFrame, code: str, period: str, days: int) -> pd.DataFrame:
        """为 K 线数据动态贴合 Baostock 级别的 Point-In-Time 基本面"""
        if period != 'daily' or df.empty:
            return df

        # 如果从 MySQL 本地就已经读取到了内置的 pe 和 pb，则直接跳过巨慢无比的远程请求
        # isST 已通过 load_klines 从 daily_kline.is_st 字段自动加载，无需单独处理
        if 'pe' in df.columns and 'pb' in df.columns and (df['pe'].sum() > 0 or df['pb'].sum() > 0):
            if 'isST' not in df.columns:
                df['isST'] = 0  # 仅当确实无来源时兜底
            return df

        try:
            from data.baostock_fetcher import BaostockFetcher
            if not hasattr(self, '_bs_fetcher'):
                self._bs_fetcher = BaostockFetcher()
            bs_df = self._bs_fetcher.get_historical_fundamentals(code, days + 60)
            if not bs_df.empty:
                df['日期'] = pd.to_datetime(df['日期'])
                df = pd.merge(df, bs_df, on='日期', how='left')
                df['pe'] = df.get('peTTM', pd.Series(0, index=df.index)).ffill().fillna(0)
                df['pb'] = df.get('pbMRQ', pd.Series(0, index=df.index)).ffill().fillna(0)
                df['isST'] = df.get('isST', pd.Series(0, index=df.index)).ffill().fillna(0)
        except Exception as e:
            logger.error(f"融合宝矿数据异常: {e}")
        return df

    # ===== 统一入口 =====

    def get_stock_data(
        self,
        symbol: str,
        period: str = 'daily',
        days: int | None = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        获取股票行情数据（东财优先，新浪备用）

        Args:
            symbol: 股票代码（如 '000001'）
            period: 'daily' / 'weekly' / 'monthly'
            days: 数据条数（默认取配置值）
            use_cache: 是否使用缓存

        Returns:
            DataFrame 包含列: 日期, 开盘, 收盘, 最高, 最低, 成交量, 涨跌幅
        """
        code = symbol.split('.')[0]
        if days is None:
            days = config.data.default_days

        if use_cache:
            # 1. 优先读取 SQLite (仅支持日线)
            if period == 'daily':
                db_df = self.db.load_klines(code, days)
                # SQLite 有数据就用，不要因为天数不够就回退到网络
                # （本地数据从 2016 年起约 2400 条，days=3650 时不满足 80% 阈值
                #   会导致全部走东财 API 拉 10+ 年数据，引发数据不一致）
                if db_df is not None and len(db_df) >= min(200, int(days * 0.3)):
                    if len(db_df) > days:
                        db_df = db_df.tail(days).reset_index(drop=True)
                    logger.debug(f"本地数据库 命中: {code} {period} {len(db_df)}条")
                    return self._apply_baostock_fundamentals(db_df, code, period, days)

            # 2. 其次读取文件短时缓存
            cached = self._load_cache(code, period, days)
            if cached is not None:
                return self._apply_baostock_fundamentals(cached, code, period, days)

        logger.warning(f"🚨 本地数据库缺少 {code} 的充足数据！为了防止策略被限流卡死，已开启【纯本地模式】，跳过该股票处理。请后续通过专用脚本补齐数据。")

        # [被注释的远程拉取代码 - 封禁/限流期间直接停用]
        # df = self._fetch_eastmoney(code, period, days)
        # if df.empty and period != 'monthly':
        #     logger.warning(f"东财失败，回退到新浪（有1023条上限）: {code} {period}")
        #     df = self._fetch_sina(code, period, days)
        #
        # if not df.empty:
        #     self._save_cache(code, period, days, df)
        #
        # return self._apply_baostock_fundamentals(df, code, period, days)

        return pd.DataFrame()

    def get_realtime_quote(self, symbol: str) -> dict:
        """获取实时行情（腾讯接口）"""
        code = symbol.split('.')[0]
        full_code = _to_full_code(code)

        try:
            url = f"http://qt.gtimg.cn/q={full_code}"
            resp = _session.get(url, timeout=10)

            text = resp.text
            if f'v_{full_code}=' in text:
                data_str = text.split('"')[1]
                parts = data_str.split('~')

                return {
                    '代码': code,
                    '名称': parts[1],
                    '最新价': float(parts[3]),
                    '昨收价': float(parts[4]) if len(parts) > 4 and parts[4] else 0,
                    '涨跌幅': float(parts[5]),
                    '成交量': float(parts[6]),
                    '成交额': float(parts[37]) if len(parts) > 37 else 0,
                }

            return {}

        except Exception as e:
            logger.error(f"实时行情获取失败: {e}")
            return {}

    def get_multi_period_data(
        self,
        symbol: str,
        daily_days: int | None = None,
        weekly_days: int | None = None,
    ) -> dict[str, pd.DataFrame]:
        """
        获取多周期数据（策略专用）

        Returns:
            {'daily': df_daily, 'weekly': df_weekly}
        """
        result = {}
        result['daily'] = self.get_stock_data(symbol, 'daily', daily_days or 120)
        result['weekly'] = self.get_stock_data(symbol, 'weekly', weekly_days or 52)
        return result
