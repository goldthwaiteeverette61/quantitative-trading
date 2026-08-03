"""
Baostock 基本面历史数据获取器

用于突破免费 API 快照限制的“Point-In-Time”真实基本面溯源模块。
1. 提供历史上每日的 peTTM, pbMRQ, isST 数据。
2. 将数据缓存在 SQLite 中，大幅提升系统回测速度。
"""

import sqlite3
import pandas as pd
import baostock as bs
import time
from datetime import datetime, timedelta
from pathlib import Path
import threading
from loguru import logger

class BaostockFetcher:
    # Baostock module is not thread-safe; requires global lock across all instances
    _bs_lock = threading.Lock()
    _logged_in = False  # 全局登录状态

    def __init__(self, db_path: str = "data/baostock_fundamentals.db"):
        self.db_path = Path("data") / "baostock_fundamentals.db" if db_path == "data/baostock_fundamentals.db" else Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._attempted_symbols = set()
        # 全局只 login 一次，不再每次查询都 login/logout
        self._ensure_login()

    @classmethod
    def _ensure_login(cls):
        """确保 Baostock 全局登录（只执行一次）"""
        with cls._bs_lock:
            if not cls._logged_in:
                bs.login()
                cls._logged_in = True
                # 注册进程退出时自动 logout
                import atexit
                atexit.register(cls._global_logout)
                logger.info("[Baostock] 全局长连接已建立")

    @classmethod
    def _global_logout(cls):
        """进程退出时 logout"""
        try:
            if cls._logged_in:
                bs.logout()
                cls._logged_in = False
                logger.info("[Baostock] 全局连接已关闭")
        except Exception:
            pass

    def _init_db(self):
        # 建立一张通用的基本面大表记录全部历史数据
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fundamental_cache (
                    symbol TEXT,
                    date TEXT,
                    peTTM REAL,
                    pbMRQ REAL,
                    isST INTEGER,
                    PRIMARY KEY (symbol, date)
                )
            ''')
            # 给日期加快索引
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_fc_date on fundamental_cache(date)')

            # 基本面大表（用于存放不变的属性如 ipoDate）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS stock_basic (
                    symbol TEXT PRIMARY KEY,
                    code_name TEXT,
                    ipoDate TEXT
                )
            ''')
            conn.commit()

    def _get_bs_code(self, symbol: str) -> str:
        """转换 6 位数字代码为 baostock 的带市场前缀代码"""
        symbol = str(symbol).strip()
        if symbol.startswith('6'):
            return f"sh.{symbol}"
        elif symbol.startswith('0') or symbol.startswith('3'):
            return f"sz.{symbol}"
        elif symbol.startswith('8') or symbol.startswith('4') or symbol.startswith('9'):
            return f"bj.{symbol}"  # baostock 并非完美支持北京，但给出兼容处理
        return symbol

    def _should_fetch(self, symbol: str, start_date: str, end_date: str) -> bool:
        """检查数据库里是不是已经拥有指定日期区间的数据，避免重复调接口"""
        if symbol in self._attempted_symbols:
            # 统一在一个进程内只重试一次，避免无数据股票反复调用
            return False

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT MAX(date) FROM fundamental_cache WHERE symbol = ?', (symbol,))
            res = cursor.fetchone()
            if not res or not res[0]:
                self._attempted_symbols.add(symbol)
                return True

            latest_db_date = res[0]
            if latest_db_date < end_date:
                self._attempted_symbols.add(symbol)
                return True
            return False

    def get_historical_fundamentals(self, symbol: str, days: int = 3650) -> pd.DataFrame:
        """
        获取过去指定天数的历史基本面切片（支持全缓存提速）
        返回 DataFrame, columns = ['日期', 'peTTM', 'pbMRQ', 'isST']
        """
        symbol = str(symbol).strip().zfill(6)
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # 1. 检查是否需要更新缓存
        if self._should_fetch(symbol, start_date, end_date):
            # 获取本地最新的日期，如果是增量，只下载缺失的部分
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT MAX(date) FROM fundamental_cache WHERE symbol = ?', (symbol,))
                res = cursor.fetchone()
                fetch_start = start_date
                if res and res[0]:
                    try:
                        latest_date = datetime.strptime(res[0], "%Y-%m-%d")
                        fetch_start = max(start_date, (latest_date + timedelta(days=1)).strftime("%Y-%m-%d"))
                    except ValueError:
                        pass

            if fetch_start <= end_date:
                self._fetch_and_cache(symbol, fetch_start, end_date)

        # 2. 从本地 SQLite 取出结果
        with sqlite3.connect(self.db_path) as conn:
            query = """
                SELECT date, peTTM, pbMRQ, isST
                FROM fundamental_cache
                WHERE symbol = ? AND date >= ? AND date <= ?
                ORDER BY date ASC
            """
            df = pd.read_sql_query(query, conn, params=(symbol, start_date, end_date))

        if df.empty:
            return pd.DataFrame(columns=['日期', 'peTTM', 'pbMRQ', 'isST'])

        # 根据系统标准重命名列
        df.rename(columns={'date': '日期'}, inplace=True)
        # 强转数字类型
        for col in ['peTTM', 'pbMRQ', 'isST']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 设置日期类型，适配 engine 的处理
        df['日期'] = pd.to_datetime(df['日期'])
        return df

    def _fetch_and_cache(self, symbol: str, start_date: str, end_date: str):
        """底层的 baostock 爬取与 SQLite 入库操作（全局长连接，只加锁不 login/logout）"""
        bs_code = self._get_bs_code(symbol)

        # 加锁保护 baostock 这个不支持并发的库（但不再 login/logout）
        with self._bs_lock:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,peTTM,pbMRQ,isST",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3"
            )

            if rs.error_code != '0':
                logger.error(f"[Baostock] 获取 {symbol} 失败: {rs.error_msg}")
                # 应对 Baostock 长连接断开/超时的问题：自动强制重新登录一次并重试
                if "未登录" in rs.error_msg:
                    logger.warning("[Baostock] 检测到会话超时，尝试自动重连...")
                    BaostockFetcher._logged_in = False
                    BaostockFetcher._ensure_login()
                    # 重新请求一次
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        "date,peTTM,pbMRQ,isST",
                        start_date=start_date,
                        end_date=end_date,
                        frequency="d",
                        adjustflag="3"
                    )
                    if rs.error_code != '0':
                        logger.error(f"[Baostock] 重试依然失败: {rs.error_msg}")
                        return
                else:
                    return

            data_list = []
            while (rs.error_code == '0') & rs.next():
                data_list.append(rs.get_row_data())

        if not data_list:
            return

        df = pd.DataFrame(data_list, columns=rs.fields)
        df['symbol'] = symbol

        # 为了稳定，强制类型清洗
        df['peTTM'] = pd.to_numeric(df['peTTM'], errors='coerce').fillna(0)
        df['pbMRQ'] = pd.to_numeric(df['pbMRQ'], errors='coerce').fillna(0)
        df['isST'] = pd.to_numeric(df['isST'], errors='coerce').fillna(0)

        # 全量 Upsert (替换/忽略冲突插入)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            records = df[['symbol', 'date', 'peTTM', 'pbMRQ', 'isST']].values.tolist()
            cursor.executemany('''
                INSERT OR REPLACE INTO fundamental_cache (symbol, date, peTTM, pbMRQ, isST)
                VALUES (?, ?, ?, ?, ?)
            ''', records)
            conn.commit()

        logger.debug(f"[Baostock] 更新缓存成功: {symbol} 共 {len(df)} 条")

    def update_stock_basic_info(self):
        """一次性从 baostock 拉取全市场的股票基础信息（包含上市日期）并入库"""
        self._ensure_login()
        with self._bs_lock:
            logger.info("[Baostock] 开始全局拉取并更新全市场股票基础信息 (IPO Date等)...")
            rs = bs.query_stock_basic(code="", code_name="")
            data_list = []
            while (rs.error_code == '0') & rs.next():
                data_list.append(rs.get_row_data())

            if not data_list:
                logger.error("[Baostock] 获取全市场基础信息失败")
                return

            records = []
            # rs.fields = ['code', 'code_name', 'ipoDate', 'outDate', 'type', 'status']
            for row in data_list:
                # 只取 A 股 (type = 1 包含主板、中小板、创业板，但 baostock 也有把北交等混杂的情况)
                # row[0]="sh.600000", 我们只截取末尾 6 位数字
                bs_code = row[0]
                symbol = bs_code.split('.')[-1]
                code_name = row[1]
                ipoDate = row[2]
                outDate = row[3]

                if len(symbol) == 6 and symbol.isdigit() and not outDate: # 排除已退市的
                    records.append((symbol, code_name, ipoDate))

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany('''
                INSERT OR REPLACE INTO stock_basic (symbol, code_name, ipoDate)
                VALUES (?, ?, ?)
            ''', records)
            conn.commit()
        logger.info(f"[Baostock] 成功缓存 {len(records)} 只股票的基本信贷(含 ipoDate)")
