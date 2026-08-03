"""
前复权价格批量更新脚本

功能：使用 AKShare 的 stock_zh_a_hist(adjust='qfq') 接口，
      仅更新 MySQL daily_kline 表中的 OHLC 四列为前复权价格，
      保留 pe/pb/circ_mv 等基本面截面字段不变。

特性：
  - 断点续传：进度写入 checkpoint 文件，中断后可从上次位置继续
  - 防封禁：每次请求间随机 sleep 1.5~3.5 秒
  - 日志：输出到 logs/qfq_update.log 和控制台

用法：
  cd /opt/quantitative-trading
  .venv/bin/python scripts/update_qfq_prices.py

  # 后台挂机运行：
  nohup .venv/bin/python scripts/update_qfq_prices.py > logs/qfq_update.log 2>&1 &
"""
import sys
import os
import time
import json
import random
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

# mock curl_cffi 防止动态库加载错误
sys.modules['curl_cffi'] = MagicMock()
sys.modules['curl_cffi.requests'] = MagicMock()

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

import akshare as ak
import pymysql
import pandas as pd
from loguru import logger

# ======== 配置 ========
MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'root123456',
    'database': 'quant_trading',
    'charset': 'utf8mb4',
}
CHECKPOINT_FILE = ROOT / 'data' / 'qfq_checkpoint.json'
LOG_FILE = ROOT / 'logs' / 'qfq_update.log'
BATCH_SIZE = 500  # 每批 UPDATE 的行数
MIN_SLEEP = 1.5   # 最小请求间隔（秒）
MAX_SLEEP = 3.5   # 最大请求间隔（秒）


def setup_logger():
    """配置日志"""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stdout, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level:^7}</level> | {message}")
    logger.add(str(LOG_FILE), level="DEBUG", rotation="50 MB",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level:^7} | {message}")


def get_all_symbols() -> list[str]:
    """从 MySQL 获取所有 distinct symbol"""
    conn = pymysql.connect(**MYSQL_CONFIG)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT symbol FROM daily_kline ORDER BY symbol")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def load_checkpoint() -> dict:
    """加载断点进度"""
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, 'r') as f:
            return json.load(f)
    return {'completed': [], 'failed': [], 'last_updated': ''}


def save_checkpoint(ckpt: dict):
    """保存断点进度"""
    ckpt['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(ckpt, f, ensure_ascii=False, indent=2)


def to_ak_symbol(symbol: str) -> str:
    """
    将纯数字代码转换为 AKShare stock_zh_a_daily 所需的格式
    stock_zh_a_daily: sz000001 / sh600000
    stock_zh_a_hist: 000001 (纯数字即可)
    """
    if symbol.startswith(('6', '9')):
        return f"sh{symbol}"
    else:
        return f"sz{symbol}"


def fetch_qfq_data(symbol: str) -> pd.DataFrame | None:
    """
    获取前复权 OHLC 数据

    优先使用 stock_zh_a_daily（新浪源，更稳定）
    失败时回退到 stock_zh_a_hist（东财源）
    """
    ak_sym = to_ak_symbol(symbol)

    # 方法 1：stock_zh_a_daily（新浪）
    try:
        df = ak.stock_zh_a_daily(symbol=ak_sym, start_date='19900101',
                                  end_date=datetime.now().strftime('%Y%m%d'),
                                  adjust='qfq')
        if df is not None and not df.empty:
            # 标准化列名
            df = df.rename(columns={
                'date': 'date', 'open': 'open', 'high': 'high',
                'low': 'low', 'close': 'close'
            })
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            return df[['date', 'open', 'high', 'low', 'close']]
    except Exception as e:
        logger.debug(f"[新浪] {symbol} 失败: {str(e)[:80]}")

    # 方法 2：stock_zh_a_hist（东财）
    try:
        df2 = ak.stock_zh_a_hist(symbol=symbol, period='daily',
                                  start_date='19900101',
                                  end_date=datetime.now().strftime('%Y%m%d'),
                                  adjust='qfq')
        if df2 is not None and not df2.empty:
            df2 = df2.rename(columns={
                '日期': 'date', '开盘': 'open', '最高': 'high',
                '最低': 'low', '收盘': 'close'
            })
            df2['date'] = pd.to_datetime(df2['date']).dt.strftime('%Y-%m-%d')
            return df2[['date', 'open', 'high', 'low', 'close']]
    except Exception as e:
        logger.debug(f"[东财] {symbol} 失败: {str(e)[:80]}")

    return None


def update_symbol_prices(symbol: str, qfq_df: pd.DataFrame) -> int:
    """
    将前复权 OHLC 写入 MySQL，只更新 open/close/high/low 四列。

    Returns: 更新的行数
    """
    conn = pymysql.connect(**MYSQL_CONFIG)
    updated = 0
    try:
        cursor = conn.cursor()

        # 分批次 UPDATE
        rows = qfq_df[['open', 'high', 'low', 'close', 'date']].values.tolist()

        for batch_start in range(0, len(rows), BATCH_SIZE):
            batch = rows[batch_start:batch_start + BATCH_SIZE]

            # 使用 CASE WHEN 批量更新（比逐行 UPDATE 快 10 倍+）
            # 但 MySQL 对太大的 CASE 可能慢，还是用 executemany 更稳
            sql = """
                UPDATE daily_kline
                SET `open` = %s, high = %s, low = %s, `close` = %s
                WHERE symbol = %s AND date = %s
            """
            data = [(row[0], row[1], row[2], row[3], symbol, row[4])
                    for row in batch]
            cursor.executemany(sql, data)
            updated += cursor.rowcount

        # 更新完 OHLC 后，基于前复权 close 重新计算 pre_close 和 change_pct
        # 使用窗口函数（MySQL 8.0+）
        cursor.execute("""
            UPDATE daily_kline dk
            INNER JOIN (
                SELECT symbol, date,
                       LAG(`close`) OVER (PARTITION BY symbol ORDER BY date) AS prev_close
                FROM daily_kline
                WHERE symbol = %s
            ) t ON dk.symbol = t.symbol AND dk.date = t.date
            SET dk.pre_close = COALESCE(t.prev_close, dk.`close`),
                dk.change_amount = dk.`close` - COALESCE(t.prev_close, dk.`close`),
                dk.change_pct = CASE
                    WHEN COALESCE(t.prev_close, 0) > 0
                    THEN (dk.`close` - t.prev_close) / t.prev_close
                    ELSE 0
                END
            WHERE dk.symbol = %s
        """, (symbol, symbol))

        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    return updated


def main():
    setup_logger()

    logger.info("=" * 60)
    logger.info("  前复权价格批量更新脚本 - 启动")
    logger.info("=" * 60)

    # 1. 获取所有 symbol
    all_symbols = get_all_symbols()
    logger.info(f"MySQL 中共 {len(all_symbols)} 只股票")

    # 2. 加载断点
    ckpt = load_checkpoint()
    completed_set = set(ckpt['completed'])
    failed_list = ckpt['failed']
    logger.info(f"断点: 已完成 {len(completed_set)} 只, 已失败 {len(failed_list)} 只")

    # 3. 过滤掉已完成的
    todo = [s for s in all_symbols if s not in completed_set]
    logger.info(f"本次需处理: {len(todo)} 只")

    if not todo:
        logger.success("所有股票已处理完毕！无需再次运行。")
        return

    # 4. 逐只处理
    success_count = 0
    fail_count = 0
    start_time = time.time()

    for i, symbol in enumerate(todo):
        progress = f"[{i+1}/{len(todo)}]"
        elapsed = time.time() - start_time
        if i > 0:
            eta_seconds = elapsed / i * (len(todo) - i)
            eta_str = f"ETA {eta_seconds/3600:.1f}h"
        else:
            eta_str = "ETA 计算中..."

        try:
            # 获取前复权数据
            qfq_df = fetch_qfq_data(symbol)

            if qfq_df is None or qfq_df.empty:
                logger.warning(f"{progress} {symbol} 获取前复权数据为空，跳过 | {eta_str}")
                failed_list.append(symbol)
                fail_count += 1
                # 即使失败也标记为 completed，避免反复重试无数据的退市股
                completed_set.add(symbol)
                ckpt['completed'] = list(completed_set)
                ckpt['failed'] = failed_list
                if i % 10 == 0:
                    save_checkpoint(ckpt)
                continue

            # 更新 MySQL
            updated = update_symbol_prices(symbol, qfq_df)

            logger.info(
                f"{progress} ✅ {symbol}: "
                f"AKShare {len(qfq_df)}条 → MySQL 更新 {updated} 行 | {eta_str}"
            )

            success_count += 1
            completed_set.add(symbol)
            ckpt['completed'] = list(completed_set)

            # 每 20 只保存一次 checkpoint
            if i % 20 == 0:
                save_checkpoint(ckpt)

        except Exception as e:
            logger.error(f"{progress} ❌ {symbol} 失败: {str(e)[:120]}")
            failed_list.append(symbol)
            fail_count += 1
            ckpt['failed'] = failed_list

            # 如果连续失败可能是被封禁，强制休眠
            if fail_count > 3 and fail_count == success_count:
                logger.warning("连续失败过多，强制休眠 60 秒...")
                time.sleep(60)

        # 防封禁：随机间隔
        sleep_time = random.uniform(MIN_SLEEP, MAX_SLEEP)
        time.sleep(sleep_time)

    # 5. 最终保存
    save_checkpoint(ckpt)

    total_time = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"  全部完成！耗时 {total_time/3600:.1f} 小时")
    logger.info(f"  成功: {success_count} | 失败: {fail_count}")
    logger.info(f"  累计完成: {len(completed_set)} / {len(all_symbols)}")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
