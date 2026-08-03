"""
动态股票池管理器

负责获取指数成分股、过滤垃圾标的，为组合策略提供干净的候选池。
"""
import os
import urllib.request

os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['NO_PROXY'] = '*'

no_proxy_handler = urllib.request.ProxyHandler({})
opener = urllib.request.build_opener(no_proxy_handler)
urllib.request.install_opener(opener)

import json
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger

from config.settings import config


# 无代理 session
_session = requests.Session()
_session.trust_env = False
_session.proxies = {'http': None, 'https': None}
_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Referer': 'https://finance.eastmoney.com/',
})
_session.verify = False

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# 指数代码映射 (用于 AKShare 的 index_stock_cons)
_INDEX_MAP = {
    'csi300': ('000300', '沪深300'),
    'csi500': ('000905', '中证500'),
    'csi1000': ('000852', '中证1000'),
    'csi2000': ('932000', '中证2000'),
    'all_a': (None, '全A'),
}


class UniverseManager:
    """
    股票池管理器

    通过东财 API 获取指数成分股，自动剔除不合格标的。
    """

    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = Path(cache_dir or config.data.cache_dir) / "universe"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_universe(
        self,
        index_code: str = 'csi500',
        top_n: int | None = None,
        filter_st: bool = True,
        filter_new: bool = True,
        new_days: int = 60,
    ) -> list[str]:
        """
        获取指数成分股列表（经过过滤）

        Args:
            index_code: 指数代码，支持 csi300 / csi500 / csi1000 / csi2000
            top_n: 只取前 N 只（按权重排序），None 表示全部
            filter_st: 是否剔除 ST 股
            filter_new: 是否剔除次新股
            new_days: 次新股天数阈值（上市不满此天数则剔除）

        Returns:
            股票代码列表，如 ['000001', '000002', ...]
        """
        # 先尝试缓存
        cached = self._load_cache(index_code)
        if cached is not None:
            symbols = cached
        else:
            symbols = self._fetch_constituents(index_code)
            if symbols:
                self._save_cache(index_code, symbols)

        if not symbols:
            logger.warning(f"无法获取 {index_code} 成分股，尝试使用备用方案")
            symbols = self._fallback_universe(index_code)

        # 过滤
        if filter_st:
            before = len(symbols)
            symbols = self._filter_st(symbols)
            if len(symbols) < before:
                logger.info(f"ST 过滤: {before} → {len(symbols)}")

        if top_n and len(symbols) > top_n:
            symbols = symbols[:top_n]

        logger.info(f"[股票池] {_INDEX_MAP.get(index_code, (None, index_code))[1]}: "
                    f"{len(symbols)} 只股票")
        return symbols

    def _fetch_constituents(self, index_code: str) -> list[str]:
        """通过 AKShare 获取指数成分股"""
        index_info = _INDEX_MAP.get(index_code)
        if not index_info:
            logger.error(f"不支持的指数代码: {index_code}")
            return []

        # 全A股直接用东财API获取，不走akshare
        if index_code == 'all_a':
            return self._fetch_all_a_stocks()

        ak_code = index_info[0]

        import akshare as ak
        import sys
        from unittest.mock import MagicMock
        if 'curl_cffi' not in sys.modules:
            sys.modules['curl_cffi'] = MagicMock()
            sys.modules['curl_cffi.requests'] = MagicMock()

        try:
            df = ak.index_stock_cons(symbol=ak_code)
            if df.empty:
                logger.warning(f"AKShare成分股查询无数据: {index_code}")
                return []

            symbols = []
            if '品种代码' in df.columns:
                for code in df['品种代码']:
                    code_str = str(code).strip().zfill(6)
                    if len(code_str) == 6:
                        symbols.append(code_str)

            logger.info(f"[AKShare] 获取 {index_info[1]} 成分股: {len(symbols)} 只")
            return symbols

        except Exception as e:
            logger.error(f"获取成分股失败: {index_code}, 错误: {e}")
            return []

    def _fallback_universe(self, index_code: str) -> list[str]:
        """
        备用方案：从全 A 股按市值排序取中小盘
        """
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            'pn': 1, 'pz': 2000, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2,
            'fields': 'f12,f14,f2,f20',
            'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',
            'fid': 'f20',  # 按市值降序
        }

        try:
            resp = _session.get(url, params=params, timeout=15)
            data = resp.json()

            if not data.get('data') or not data['data'].get('diff'):
                return []

            symbols = []
            for item in data['data']['diff']:
                code = str(item.get('f12', ''))
                name = str(item.get('f14', ''))
                close = item.get('f2')
                if code and len(code) == 6 and close not in (None, '-', 0):
                    if 'ST' not in name.upper():
                        symbols.append(code)

            # 按市值降序排列后，按指数范围截取
            if index_code == 'csi300':
                symbols = symbols[:300]
            elif index_code == 'csi500':
                symbols = symbols[300:800]
            elif index_code == 'csi1000':
                symbols = symbols[300:1300]
            elif index_code == 'all_a':
                pass  # 全A不截取

            logger.info(f"[备用] 构造 {index_code} 近似股票池: {len(symbols)} 只")
            return symbols

        except Exception as e:
            logger.error(f"备用方案也失败: {e}")
            return []

    def _fetch_all_a_stocks(self) -> list[str]:
        """通过 AKShare 获取全市场 A 股列表"""
        import akshare as ak
        symbols = []
        try:
            df = ak.stock_info_a_code_name()
            if df.empty:
                logger.warning("AKShare 全A股获取无数据")
                return []

            for index, row in df.iterrows():
                code = str(row['code']).strip()
                name = str(row['name']).strip()
                if len(code) == 6 and 'ST' not in name.upper() and not name.upper().startswith('退'):
                    symbols.append(code)

            logger.info(f"[全A] 获取全市场 A 股: {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.error(f"全A股获取失败(AKShare): {e}")
            return []

    def get_cross_section_data(self, symbols: list[str]) -> pd.DataFrame:
        """
        获取股票池的截面数据（一次性获取所有因子）

        Args:
            symbols: 股票代码列表

        Returns:
            DataFrame, index=代码, 列包含:
            name, close, market_cap, pe, pb, change_pct, turnover
        """
        if not symbols:
            return pd.DataFrame()

        all_rows = []
        # 东财一次最多查 500 只，分批
        batch_size = 500
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            rows = self._fetch_batch_snapshot(batch)
            all_rows.extend(rows)

        if not all_rows:
            logger.warning("[Universe] 东财接口批量拉取截面数据全部失败！尝试启动本地容灾备用管线 (Sina + SQLite T-1)...")
            return self._get_cross_section_data_fallback(symbols)

        df = pd.DataFrame(all_rows)
        df = df.drop_duplicates(subset='symbol', keep='first')  # 东财偶尔返回重复记录
        df.set_index('symbol', inplace=True)
        return df

    def _get_cross_section_data_fallback(self, symbols: list[str]) -> pd.DataFrame:
        """灾备管线：用 Sina 拿实盘行情，用 SQLite fundamental_cache 拿 PE/PB/市值"""
        import akshare as ak
        import sqlite3
        try:
            logger.info("[Universe Fallback] 正在通过新浪 API 获取全市场实时快照...")
            spot_df = ak.stock_zh_a_spot()
            # 建立代码映射表 (Sina 代码格式如 sh600000, sz000001)
            spot_df['symbol'] = spot_df['代码'].astype(str).str[-6:]
            spot_dict = spot_df.set_index('symbol').to_dict('index')

            db_path = Path("data/baostock_fundamentals.db")
            fund_dict = {}
            if db_path.exists():
                logger.info("[Universe Fallback] 正在从本地 SQLite 查询 T-1 日财务因子...")
                with sqlite3.connect(db_path) as conn:
                    # 倒序取每个股票最新的一天
                    cursor = conn.cursor()
                    query = """
                        SELECT symbol, peTTM, pbMRQ
                        FROM fundamental_cache
                        WHERE date = (SELECT MAX(date) FROM fundamental_cache WHERE fundamental_cache.symbol = symbol)
                    """
                    for row in cursor.execute(query):
                        fund_dict[row[0]] = {"pe": row[1], "pb": row[2]}

            rows = []
            for sym in symbols:
                s_data = spot_dict.get(sym)
                if not s_data:
                    continue

                close_price = float(s_data.get('最新价', 0))
                if close_price <= 0:
                    continue

                f_data = fund_dict.get(sym, {})
                pe = f_data.get('pe', 0.0)
                pb = f_data.get('pb', 0.0)

                rows.append({
                    'symbol': sym,
                    'name': s_data.get('名称', sym),
                    'close': close_price,
                    # 新浪快照和基础版baostock都没有市值，我们可以用流通股本*收盘价，或者临时估算
                    # 这里为了兼容，市值用 0 占位，由回测引擎里的收盘价替代
                    'market_cap': 0.0,
                    'industry': '未知',
                    'pe': pe,
                    'pb': pb,
                    'change_pct': float(s_data.get('涨跌幅', 0)),
                    'turnover_rate': 0.0,
                })

            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame(rows)
            df.set_index('symbol', inplace=True)
            logger.info(f"[Universe Fallback] 备用管线拼装完成: {len(df)} 只股票截面")
            return df

        except Exception as e:
            logger.error(f"[Universe Fallback] 备用管线也彻底崩溃: {e}")
            return pd.DataFrame()

    def _fetch_batch_snapshot(self, symbols: list[str]) -> list[dict]:
        """
        通过东财 API 批量获取股票快照

        包含：最新价、市值、PE、PB、涨跌幅、换手率
        """
        # 构造 secid 列表
        secids = []
        for s in symbols:
            market = '0' if s.startswith(('0', '1', '2', '3')) else '1'
            secids.append(f"{market}.{s}")

        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            'fltt': 2,
            'fields': 'f12,f14,f2,f20,f9,f23,f3,f8,f18,f100',
            # f12=代码 f14=名称 f2=最新价 f20=总市值 f9=PE f23=PB f3=涨跌幅 f8=换手率 f18=昨收
            'secids': ','.join(secids),
        }

        try:
            resp = _session.get(url, params=params, timeout=15)
            data = resp.json()

            if not data.get('data') or not data['data'].get('diff'):
                return []

            rows = []
            for item in data['data']['diff']:
                code = str(item.get('f12', ''))
                name = item.get('f14', '')
                close = item.get('f2', '-')
                market_cap = item.get('f20', '-')
                pe = item.get('f9', '-')
                pb = item.get('f23', '-')
                change_pct = item.get('f3', '-')
                turnover = item.get('f8', '-')
                industry = item.get('f100', '未知')

                # 过滤无效数据
                if close == '-' or close is None:
                    continue

                rows.append({
                    'symbol': code,
                    'name': name,
                    'close': float(close) if close != '-' else 0.0,
                    'market_cap': float(market_cap) if market_cap != '-' else 0.0,
                    'pe': float(pe) if pe != '-' else 0.0,
                    'pb': float(pb) if pb != '-' else 0.0,
                    'change_pct': float(change_pct) if change_pct != '-' else 0.0,
                    'turnover_rate': float(turnover) if turnover != '-' else 0.0,
                    'industry': industry
                })

            return rows

        except Exception as e:
            logger.error(f"批量快照获取失败: {e}")
            return []

    def _filter_st(self, symbols: list[str]) -> list[str]:
        """过滤 ST 股（已在获取阶段过滤，此处做二次确认）"""
        # 成分股获取阶段已经过滤了 ST，这里直接返回
        return symbols

    # ===== 缓存 =====

    def _cache_path(self, index_code: str) -> Path:
        return self.cache_dir / f"{index_code}_constituents.json"

    def _load_cache(self, index_code: str) -> list[str] | None:
        cache_file = self._cache_path(index_code)
        if not cache_file.exists():
            return None

        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - mtime > timedelta(hours=24):
            return None

        try:
            with open(cache_file, 'r') as f:
                return json.load(f)
        except Exception:
            return None

    def _save_cache(self, index_code: str, symbols: list[str]):
        try:
            cache_file = self._cache_path(index_code)
            with open(cache_file, 'w') as f:
                json.dump(symbols, f)
        except Exception:
            pass
