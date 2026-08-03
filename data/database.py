
import pandas as pd
from pathlib import Path
from loguru import logger
from typing import Optional
import threading

from config.settings import config


class DatabaseManager:
    """数据管理器 — 高性能原生 MySQL 8.0 核心中台（单例模式 + MySQL 连接池）"""
    _instance = None
    _initialized = False
    _pool = None  # MySQL 连接池

    def __new__(cls, db_path: str | None = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str | None = None):
        if DatabaseManager._initialized:
            return

        self._db_type = 'mysql'

        import pymysql
        self._mysql_config = {
            'host': config.data.mysql_host,
            'port': config.data.mysql_port,
            'user': config.data.mysql_user,
            'password': config.data.mysql_password,
            'database': config.data.mysql_database,
            'charset': 'utf8mb4',
            'autocommit': True,
        }
        # 首次连接时确保数据库存在
        self._ensure_mysql_database()
        # 初始化连接池
        self._init_pool()

        self._init_db()
        DatabaseManager._initialized = True
        logger.info("DatabaseManager 单例初始化完成（MySQL 连接池已就绪）")

    # ================= 连接管理 =================

    def _init_pool(self):
        """初始化 MySQL 连接池"""
        import pymysql
        from dbutils.pooled_db import PooledDB
        DatabaseManager._pool = PooledDB(
            creator=pymysql,
            mincached=3,       # 最小空闲连接数
            maxcached=10,      # 最大空闲连接数
            maxconnections=20, # 最大连接数
            blocking=True,     # 连接耗尽时阻塞等待
            maxusage=None,     # 单个连接最大复用次数（None=不限）
            setsession=[],     # 会话初始化命令
            **self._mysql_config
        )
        logger.info(f"MySQL 连接池已初始化: min=3, max=20")

    def _ensure_mysql_database(self):
        """确保 MySQL 数据库存在，不存在则创建"""
        import pymysql
        cfg = self._mysql_config.copy()
        db_name = cfg.pop('database')
        conn = pymysql.connect(**cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
        finally:
            conn.close()

    def _get_conn(self):
        """获取数据库连接（MySQL 从连接池获取，用完 close() 自动归还池）"""
        return DatabaseManager._pool.connection()

    # ================= 表结构初始化 =================

    def _init_db(self):
        """初始化数据库表结构"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # ---------- 日线数据表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_kline (
                    symbol VARCHAR(20) NOT NULL,
                    date VARCHAR(10) NOT NULL,
                    `open` DOUBLE DEFAULT 0,
                    `close` DOUBLE DEFAULT 0,
                    high DOUBLE DEFAULT 0,
                    low DOUBLE DEFAULT 0,
                    volume DOUBLE DEFAULT 0,
                    amount DOUBLE DEFAULT 0,
                    outstanding_share DOUBLE DEFAULT 0,
                    turnover DOUBLE DEFAULT 0,
                    PRIMARY KEY (symbol, date),
                    INDEX idx_kline_date (date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # ---------- 模拟盘账户表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    account_id VARCHAR(64) PRIMARY KEY,
                    broker_type VARCHAR(32),
                    auth_data TEXT,
                    initial_capital DOUBLE,
                    cash DOUBLE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                INSERT IGNORE INTO paper_accounts (account_id, broker_type, auth_data, initial_capital, cash)
                VALUES ('local_1', 'LOCAL', '', 1000000.0, 1000000.0)
            ''')

            # ---------- 持仓表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS paper_positions (
                    account_id VARCHAR(64), symbol VARCHAR(20),
                    shares INT, cost_price DOUBLE,
                    PRIMARY KEY (account_id, symbol)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # ---------- 交易流水表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS paper_trade_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    account_id VARCHAR(64), symbol VARCHAR(20),
                    action VARCHAR(10), shares INT,
                    price DOUBLE, amount DOUBLE,
                    commission DOUBLE, trade_time VARCHAR(32),
                    remark TEXT
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            # 兼容旧表：安全添加 remark 列
            try:
                cursor.execute("ALTER TABLE paper_trade_log ADD COLUMN remark TEXT")
            except Exception:
                pass

            # ---------- 成交信号指标扩展表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_signal_ext (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    trade_id INT NOT NULL,
                    task_id VARCHAR(64),
                    account_id VARCHAR(64),
                    strategy_key VARCHAR(128),
                    symbol VARCHAR(20),
                    signal_time VARCHAR(32),
                    metric_key VARCHAR(64),
                    metric_value_num DOUBLE DEFAULT NULL,
                    metric_value_text TEXT DEFAULT NULL,
                    created_at VARCHAR(32),
                    UNIQUE KEY uniq_trade_metric (trade_id, metric_key),
                    INDEX idx_signal_trade (trade_id),
                    INDEX idx_signal_strategy_metric (strategy_key, metric_key),
                    INDEX idx_signal_task_metric (task_id, metric_key),
                    INDEX idx_signal_symbol (symbol)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # ---------- 策略任务表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS strategy_tasks (
                    task_id VARCHAR(64) PRIMARY KEY,
                    account_id VARCHAR(64),
                    strategy_key VARCHAR(128),
                    strategy_name VARCHAR(128),
                    index_code VARCHAR(32),
                    top_n INT DEFAULT 50,
                    rebalance_days INT DEFAULT 30,
                    status VARCHAR(32) DEFAULT 'running',
                    message TEXT, rebalance_count INT DEFAULT 0,
                    last_rebalance VARCHAR(32), next_rebalance VARCHAR(32),
                    created_at VARCHAR(32),
                    allocation VARCHAR(32) DEFAULT 'even_amount',
                    fixed_lots INT DEFAULT 10,
                    stamp_tax_rate DOUBLE DEFAULT 0.0005,
                    commission_rate DOUBLE DEFAULT 0.0003,
                    slippage_rate DOUBLE DEFAULT 0.005
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # ---------- 策略采用状态表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS strategy_adoptions (
                    strategy_key VARCHAR(128) PRIMARY KEY,
                    adopted INT DEFAULT 0,
                    adopted_at VARCHAR(32),
                    status VARCHAR(32) DEFAULT 'normal'
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # ---------- 回测历史表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS backtest_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    strategy_key VARCHAR(128), strategy_name VARCHAR(128),
                    index_code VARCHAR(32), days INT,
                    initial_capital DOUBLE, final_value DOUBLE,
                    return_pct DOUBLE, annual_return_pct DOUBLE,
                    max_drawdown_pct DOUBLE, sharpe_ratio DOUBLE,
                    volatility_pct DOUBLE, total_rebalances INT,
                    top_n INT DEFAULT 30,
                    stamp_tax_rate DOUBLE DEFAULT 0.0005,
                    commission_rate DOUBLE DEFAULT 0.0003,
                    slippage_rate DOUBLE DEFAULT 0.005,
                    created_at VARCHAR(32)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            try:
                cursor.execute("ALTER TABLE backtest_history ADD COLUMN task_id VARCHAR(64)")
            except Exception:
                pass

            try:
                cursor.execute("ALTER TABLE backtest_history ADD COLUMN status VARCHAR(32) DEFAULT 'completed'")
            except Exception:
                pass

            try:
                cursor.execute("ALTER TABLE backtest_history ADD COLUMN error_msg TEXT")
            except Exception:
                pass

            try:
                cursor.execute("ALTER TABLE backtest_history ADD COLUMN start_date VARCHAR(10) DEFAULT ''")
            except Exception:
                pass

            try:
                cursor.execute("ALTER TABLE backtest_history ADD COLUMN end_date VARCHAR(10) DEFAULT ''")
            except Exception:
                pass

            # ---------- 调仓异常日志表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rebalance_anomaly_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    task_id VARCHAR(64), account_id VARCHAR(64),
                    rebalance_round INT, symbol VARCHAR(20),
                    anomaly_type VARCHAR(32), detail TEXT,
                    stock_price DOUBLE DEFAULT 0,
                    required_amount DOUBLE DEFAULT 0,
                    available_cash DOUBLE DEFAULT 0,
                    trade_date VARCHAR(10),
                    created_at VARCHAR(32),
                    INDEX idx_anomaly_task (task_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # ---------- 极限压测矩阵任务表 ----------
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS matrix_tasks (
                    id VARCHAR(64) PRIMARY KEY,
                    task_name VARCHAR(255),
                    status VARCHAR(32) DEFAULT 'pending',
                    progress INT DEFAULT 0,
                    logs LONGTEXT,
                    results_json LONGTEXT,
                    created_at VARCHAR(32),
                    updated_at VARCHAR(32)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            if self._db_type != 'mysql':
                conn.commit()

            logger.debug(f"数据库初始化成功: {self._db_type}")
        finally:
            conn.close()

    # ================= K线数据存取 =================

    def save_klines(self, symbol: str, df: pd.DataFrame):
        """保存 K 线数据"""
        if df.empty:
            return

        save_df = pd.DataFrame(index=df.index)
        save_df['symbol'] = symbol

        if '日期' in df.columns:
            save_df['date'] = pd.to_datetime(df['日期']).dt.strftime('%Y-%m-%d')
        elif 'date' in df.columns:
            save_df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        else:
            logger.error("数据缺失日期列")
            return

        col_map = {
            'open': ['开盘', 'open'], 'close': ['收盘', 'close'],
            'high': ['最高', 'high'], 'low': ['最低', 'low'],
            'volume': ['成交量', 'volume'], 'amount': ['成交额', 'amount'],
            'outstanding_share': ['流通股本', 'outstanding_share'],
            'turnover': ['纯换手率', 'turnover']
        }
        for target_col, candidates in col_map.items():
            save_df[target_col] = 0.0
            for cand in candidates:
                if cand in df.columns:
                    save_df[target_col] = pd.to_numeric(df[cand], errors='coerce').fillna(0.0)
                    break

        # MySQL: 使用 INSERT ... ON DUPLICATE KEY UPDATE（行级锁，天然无死锁）
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            sql = '''
                INSERT INTO daily_kline (symbol, date, `open`, `close`, high, low, volume, amount, outstanding_share, turnover)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `open`=VALUES(`open`), `close`=VALUES(`close`),
                    high=VALUES(high), low=VALUES(low),
                    volume=VALUES(volume), amount=VALUES(amount),
                    outstanding_share=VALUES(outstanding_share),
                    turnover=VALUES(turnover)
            '''
            rows = save_df[['symbol', 'date', 'open', 'close', 'high', 'low',
                            'volume', 'amount', 'outstanding_share', 'turnover']].values.tolist()
            cursor.executemany(sql, rows)
            conn.commit()
        finally:
            conn.close()

        logger.info(f"[DB] {symbol} 保存 {len(save_df)} 条日线数据")

    def load_klines(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """加载指定股票的最近 N 天数据"""
        code = symbol.split('.')[0]

        query_sql = '''
            SELECT date as 日期, `open` as 开盘, `close` as 收盘,
                   high as 最高, low as 最低, volume as 成交量, amount as 成交额,
                   outstanding_share, turnover, pe_ttm as pe, pb, dv_ratio, circ_mv, is_st
            FROM daily_kline
            WHERE symbol = %s
            ORDER BY date DESC LIMIT %s
        '''

        try:
            conn = self._get_conn()
            try:
                df = pd.read_sql_query(query_sql, conn, params=(code, days))
            finally:
                conn.close()

            if df.empty:
                return None

            df = df.sort_values('日期').drop_duplicates(subset=['日期'], keep='last').reset_index(drop=True)
            df['日期'] = pd.to_datetime(df['日期'])
            df['涨跌幅'] = df['收盘'].pct_change() * 100
            df['涨跌幅'] = df['涨跌幅'].fillna(0)

            if 'turnover' in df.columns:
                # turnover 在 MySQL 中已经是百分比形式（如 3.5 = 3.5%），不需要再 ×100
                df['换手率'] = df['turnover']
            else:
                df['换手率'] = 0.0
                df['turnover'] = 0.0

            if 'circ_mv' in df.columns:
                # circ_mv is often in units of original DB, convert to 100M if needed, but in our DB it's yuan
                df['market_cap'] = df['circ_mv'] / 1e8
            elif 'outstanding_share' in df.columns:
                df['market_cap'] = df['outstanding_share'] * df['收盘'] / 1e8
            else:
                df['market_cap'] = 0.0

            if 'pe' not in df.columns: df['pe'] = 0.0
            if 'pb' not in df.columns: df['pb'] = 0.0
            if 'dv_ratio' in df.columns: df['dividend_yield'] = df['dv_ratio'].fillna(0.0)

            # is_st → isST（策略层统一用 isST 列名）
            if 'is_st' in df.columns:
                df['isST'] = df['is_st'].fillna(0).astype(int)
            else:
                df['isST'] = 0

            return df
        except Exception as e:
            logger.error(f"[DB] 读取失败 {symbol}: {e}")
            return None

    def get_data_stats(self) -> dict:
        """获取数据库统计信息"""
        try:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                # 设置查询超时 10 秒
                cursor.execute("SET SESSION MAX_EXECUTION_TIME=10000")
                # 精确统计股票数量（利用主键前缀索引）
                cursor.execute("SELECT COUNT(*) FROM (SELECT symbol FROM daily_kline GROUP BY symbol) t")
                symbols_count = cursor.fetchone()[0]
                # 由于千万级别数据使用 SELECT COUNT(*) 会导致 InnoDB 全表扫描并锁死 CPU
                # 故改用 MySQL 自带的极速估算（对面板展示来说精度足够）
                cursor.execute("SHOW TABLE STATUS LIKE 'daily_kline'")
                status_row = cursor.fetchone()
                rows_count = status_row[4] if status_row else 0
            finally:
                conn.close()
            return {"total_symbols": symbols_count, "total_rows": rows_count, "status": "ok"}
        except Exception as e:
            logger.error(f"[DB] 获取统计信息失败: {e}")
            return {"total_symbols": 0, "total_rows": 0}

    # ================= 通用 SQL 执行辅助 =================

    def _placeholder(self) -> str:
        """返回当前后端的占位符"""
        return '%s'

    def _exec(self, sql: str, params=None, fetch=False, fetchall=False):
        """通用执行方法，自动管理连接"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params or ())
            if fetchall:
                result = cursor.fetchall()
                # MySQL cursor 没有 description 属性的话需要额外处理
                desc = cursor.description
                return result, desc
            if fetch:
                return cursor.fetchone()
            return cursor
        except Exception:
            raise
        finally:
            conn.close()

    def _exec_write(self, sql: str, params=None):
        """写入操作"""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params or ())
            conn.commit()
        finally:
            conn.close()

    # ================= 模拟实盘交易接口 =================

    def get_paper_accounts(self) -> list[dict]:
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT account_id, broker_type, auth_data, initial_capital, cash FROM paper_accounts")
            rows = cursor.fetchall()
            return [{"account_id": r[0], "broker_type": r[1], "auth_data": r[2], "initial_capital": r[3], "cash": r[4]} for r in rows]
        finally:
            conn.close()

    def add_or_update_paper_account(self, account_id: str, broker_type: str, auth_data: str, initial_capital: float, cash: float):
        ph = self._placeholder()
        if self._db_type == 'mysql':
            sql = f'''INSERT INTO paper_accounts (account_id, broker_type, auth_data, initial_capital, cash)
                      VALUES ({ph},{ph},{ph},{ph},{ph})
                      ON DUPLICATE KEY UPDATE broker_type=VALUES(broker_type), auth_data=VALUES(auth_data),
                      initial_capital=VALUES(initial_capital), cash=VALUES(cash)'''
        else:
            sql = f'''INSERT OR REPLACE INTO paper_accounts (account_id, broker_type, auth_data, initial_capital, cash)
                      VALUES ({ph},{ph},{ph},{ph},{ph})'''
        self._exec_write(sql, (account_id, broker_type, auth_data, initial_capital, cash))

    def update_paper_cash(self, account_id: str, new_cash: float):
        ph = self._placeholder()
        self._exec_write(f"UPDATE paper_accounts SET cash = {ph} WHERE account_id = {ph}", (new_cash, account_id))

    def get_paper_positions(self, account_id: str) -> list[dict]:
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT symbol, shares, cost_price FROM paper_positions WHERE account_id = {ph}", (account_id,))
            rows = cursor.fetchall()
            return [{"symbol": r[0], "shares": r[1], "cost_price": r[2]} for r in rows]
        finally:
            conn.close()

    def update_paper_position(self, account_id: str, symbol: str, shares: int, cost_price: float):
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if shares <= 0:
                cursor.execute(f"DELETE FROM paper_positions WHERE account_id = {ph} AND symbol = {ph}", (account_id, symbol))
            else:
                if self._db_type == 'mysql':
                    cursor.execute(f'''INSERT INTO paper_positions (account_id, symbol, shares, cost_price)
                                      VALUES ({ph},{ph},{ph},{ph})
                                      ON DUPLICATE KEY UPDATE shares=VALUES(shares), cost_price=VALUES(cost_price)''',
                                   (account_id, symbol, shares, cost_price))
                else:
                    cursor.execute(f'''INSERT OR REPLACE INTO paper_positions (account_id, symbol, shares, cost_price)
                                      VALUES ({ph},{ph},{ph},{ph})''',
                                   (account_id, symbol, shares, cost_price))
            conn.commit()
        finally:
            conn.close()

    def log_paper_trade(self, account_id: str, symbol: str, action: str, shares: int, price: float, amount: float, commission: float, trade_time: str, remark: str = '') -> int:
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f'''INSERT INTO paper_trade_log (account_id, symbol, action, shares, price, amount, commission, trade_time, remark)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})''',
                (account_id, symbol, action, shares, price, amount, commission, trade_time, remark))
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    def log_trade_signal_metrics(self, trade_id: int, task_id: str, account_id: str,
                                 strategy_key: str, symbol: str, signal_time: str,
                                 metrics: dict):
        """按 key/value 扩展表记录一笔成交对应的信号指标。"""
        if not trade_id or not metrics:
            return

        from datetime import datetime

        ph = self._placeholder()
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows = []
        for key, value in metrics.items():
            if value is None:
                continue
            metric_key = str(key)[:64]
            metric_value_num = None
            metric_value_text = None
            if isinstance(value, bool):
                metric_value_num = 1.0 if value else 0.0
            else:
                try:
                    metric_value_num = float(value)
                    if metric_value_num != metric_value_num:
                        metric_value_num = None
                except (TypeError, ValueError):
                    metric_value_text = str(value)
            rows.append((
                trade_id, task_id, account_id, strategy_key, symbol, signal_time,
                metric_key, metric_value_num, metric_value_text, created_at,
            ))

        if not rows:
            return

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            if self._db_type == 'mysql':
                cursor.executemany(
                    f'''INSERT INTO trade_signal_ext
                        (trade_id, task_id, account_id, strategy_key, symbol, signal_time,
                         metric_key, metric_value_num, metric_value_text, created_at)
                        VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})
                        ON DUPLICATE KEY UPDATE
                            task_id=VALUES(task_id),
                            account_id=VALUES(account_id),
                            strategy_key=VALUES(strategy_key),
                            symbol=VALUES(symbol),
                            signal_time=VALUES(signal_time),
                            metric_value_num=VALUES(metric_value_num),
                            metric_value_text=VALUES(metric_value_text),
                            created_at=VALUES(created_at)''',
                    rows,
                )
            else:
                cursor.executemany(
                    f'''INSERT OR REPLACE INTO trade_signal_ext
                        (trade_id, task_id, account_id, strategy_key, symbol, signal_time,
                         metric_key, metric_value_num, metric_value_text, created_at)
                        VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})''',
                    rows,
                )
            conn.commit()
        finally:
            conn.close()

    def get_paper_trade_log(self, account_id: str) -> list[dict]:
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f'''SELECT t.id, t.symbol, t.action, t.shares, t.price, t.amount, t.commission, t.trade_time, t.remark, s.code_name as name
                              FROM paper_trade_log t
                              LEFT JOIN stock_basic s ON t.symbol = s.symbol
                              WHERE t.account_id = {ph} ORDER BY t.trade_time DESC''', (account_id,))
            desc = cursor.description
            cols = [d[0] for d in desc]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ================= 策略采用状态 =================

    def get_strategy_adoptions(self) -> dict:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT strategy_key, adopted FROM strategy_adoptions")
            rows = cursor.fetchall()
            return {r[0]: bool(r[1]) for r in rows}
        finally:
            conn.close()

    def get_strategy_statuses(self) -> dict:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT strategy_key, status FROM strategy_adoptions")
            rows = cursor.fetchall()
            return {r[0]: (r[1] or 'normal') for r in rows}
        finally:
            conn.close()

    def set_strategy_status(self, strategy_key: str, status: str):
        from datetime import datetime
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT strategy_key FROM strategy_adoptions WHERE strategy_key = {ph}", (strategy_key,))
            if cursor.fetchone():
                cursor.execute(f"UPDATE strategy_adoptions SET status = {ph} WHERE strategy_key = {ph}", (status, strategy_key))
            else:
                cursor.execute(
                    f'''INSERT INTO strategy_adoptions (strategy_key, adopted, adopted_at, status)
                        VALUES ({ph}, 0, {ph}, {ph})''',
                    (strategy_key, datetime.now().isoformat(), status))
            conn.commit()
        finally:
            conn.close()

    def set_strategy_adoption(self, strategy_key: str, adopted: bool):
        from datetime import datetime
        ph = self._placeholder()
        if self._db_type == 'mysql':
            sql = f'''INSERT INTO strategy_adoptions (strategy_key, adopted, adopted_at, status)
                      VALUES ({ph}, {ph}, {ph}, 'normal')
                      ON DUPLICATE KEY UPDATE adopted=VALUES(adopted), adopted_at=VALUES(adopted_at)'''
        else:
            sql = f'''INSERT OR REPLACE INTO strategy_adoptions (strategy_key, adopted, adopted_at, status)
                      VALUES ({ph}, {ph}, {ph}, COALESCE((SELECT status FROM strategy_adoptions WHERE strategy_key = {ph}), 'normal'))'''
        params = (strategy_key, 1 if adopted else 0, datetime.now().isoformat())
        if self._db_type != 'mysql':
            params = params + (strategy_key,)
        self._exec_write(sql, params)

    # ================= 回测历史 =================

    def cleanup_dangling_backtest_tasks(self):
        """仅在 server 启动时调用：清理前次意外退出的僵尸回测任务"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("UPDATE backtest_history SET status = 'failed' WHERE status IN ('running', 'pending')")
            conn.commit()
        except Exception as e:
            logger.warning(f"清理僵尸回测任务失败: {e}")
        finally:
            conn.close()

    def create_pending_backtest(self, task_id: str, record: dict):
        from datetime import datetime
        ph = self._placeholder()
        self._exec_write(
            f'''INSERT INTO backtest_history
                (task_id, status, strategy_key, strategy_name, index_code, days, initial_capital,
                 top_n, stamp_tax_rate, commission_rate, slippage_rate, start_date, end_date, created_at)
                VALUES ({ph},'running',{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})''',
            (task_id, record.get('strategy_key', ''), record.get('strategy_name', ''),
             record.get('index_code', ''), record.get('days', 0), record.get('initial_capital', 0),
             record.get('top_n', 30), record.get('stamp_tax_rate', 0.0005),
             record.get('commission_rate', 0.0003), record.get('slippage_rate', 0.003),
             record.get('start_date', ''), record.get('end_date', ''),
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    def update_backtest_record(self, task_id: str, record: dict):
        ph = self._placeholder()
        self._exec_write(
            f'''UPDATE backtest_history SET
                 status = {ph}, final_value = {ph}, return_pct = {ph}, annual_return_pct = {ph},
                 max_drawdown_pct = {ph}, sharpe_ratio = {ph}, volatility_pct = {ph},
                 total_rebalances = {ph}, error_msg = {ph}
                WHERE task_id = {ph}''',
            (record.get('status', 'completed'), record.get('final_value', 0),
             record.get('return_pct', 0), record.get('annual_return_pct', 0),
             record.get('max_drawdown_pct', 0), record.get('sharpe_ratio', 0),
             record.get('volatility_pct', 0), record.get('total_rebalances', 0),
             record.get('error_msg', None), task_id))

    def save_backtest_record(self, record: dict):
        from datetime import datetime
        ph = self._placeholder()
        self._exec_write(
            f'''INSERT INTO backtest_history
                (strategy_key, strategy_name, index_code, days, initial_capital,
                 final_value, return_pct, annual_return_pct, max_drawdown_pct,
                 sharpe_ratio, volatility_pct, total_rebalances, top_n,
                 stamp_tax_rate, commission_rate, slippage_rate, start_date, end_date, created_at)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})''',
            (record.get('strategy_key', ''), record.get('strategy_name', ''),
             record.get('index_code', ''), record.get('days', 0),
             record.get('initial_capital', 0), record.get('final_value', 0),
             record.get('return_pct', 0), record.get('annual_return_pct', 0),
             record.get('max_drawdown_pct', 0), record.get('sharpe_ratio', 0),
             record.get('volatility_pct', 0), record.get('total_rebalances', 0),
             record.get('top_n', 30),
             record.get('stamp_tax_rate', 0.0005), record.get('commission_rate', 0.0003),
             record.get('slippage_rate', 0.003),
             record.get('start_date', ''), record.get('end_date', ''),
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    def get_backtest_history(self, limit: int = 500) -> list[dict]:
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f'''
                SELECT id, strategy_key, strategy_name, index_code, days,
                       initial_capital, final_value, return_pct, annual_return_pct,
                       max_drawdown_pct, sharpe_ratio, volatility_pct, total_rebalances, top_n,
                       stamp_tax_rate, commission_rate, slippage_rate, created_at,
                       task_id, status, error_msg, start_date, end_date
                FROM backtest_history ORDER BY id DESC LIMIT {ph}
            ''', (limit,))
            rows = cursor.fetchall()
            return [{
                "id": r[0], "strategy_key": r[1], "strategy_name": r[2],
                "index_code": r[3], "days": r[4], "initial_capital": r[5],
                "final_value": r[6], "return_pct": r[7], "annual_return_pct": r[8],
                "max_drawdown_pct": r[9], "sharpe_ratio": r[10], "volatility_pct": r[11],
                "total_rebalances": r[12], "top_n": r[13],
                "stamp_tax_rate": r[14], "commission_rate": r[15], "slippage_rate": r[16],
                "created_at": r[17],
                "task_id": r[18], "status": r[19] if r[19] else 'completed',
                "error_msg": r[20] if len(r) > 20 else None,
                "start_date": r[21] if len(r) > 21 else '',
                "end_date": r[22] if len(r) > 22 else '',
            } for r in rows]
        finally:
            conn.close()

    # ================= 调仓异常日志 =================

    def log_rebalance_anomaly(self, task_id: str, account_id: str, rebalance_round: int,
                              symbol: str, anomaly_type: str, detail: str,
                              stock_price: float = 0, required_amount: float = 0,
                              available_cash: float = 0, trade_date: str = ''):
        from datetime import datetime
        ph = self._placeholder()
        self._exec_write(
            f'''INSERT INTO rebalance_anomaly_log
                (task_id, account_id, rebalance_round, symbol, anomaly_type, detail,
                 stock_price, required_amount, available_cash, trade_date, created_at)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})''',
            (task_id, account_id, rebalance_round, symbol, anomaly_type, detail,
             stock_price, required_amount, available_cash, trade_date,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    def get_rebalance_anomalies(self, task_id: str, limit: int = 200) -> list[dict]:
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(f'''
                SELECT id, task_id, account_id, rebalance_round, symbol, anomaly_type,
                       detail, stock_price, required_amount, available_cash, trade_date, created_at
                FROM rebalance_anomaly_log WHERE task_id = {ph} ORDER BY id DESC LIMIT {ph}
            ''', (task_id, limit))
            desc = cursor.description
            cols = [d[0] for d in desc]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    def delete_rebalance_anomalies(self, task_id: str):
        ph = self._placeholder()
        self._exec_write(f'DELETE FROM rebalance_anomaly_log WHERE task_id = {ph}', (task_id,))
