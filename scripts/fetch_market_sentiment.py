"""
市场情绪数据日频采集脚本

采集三大市场温度指标并写入 MySQL market_sentiment 表：
  1. 涨停/跌停家数（东财涨停池）
  2. 北向资金净流入（沪股通+深股通汇总）
  3. 两融余额（上交所融资融券余额）

用法:
  # 采集当天
  python scripts/fetch_market_sentiment.py

  # 批量补历史
  python scripts/fetch_market_sentiment.py --start 2021-01-01 --end 2026-04-10
"""
import argparse
import time
from datetime import datetime, timedelta
from loguru import logger
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fetch_zt_dt_count(date_str: str) -> tuple[int | None, int | None]:
    """拉取指定日期涨停/跌停家数"""
    import akshare as ak
    date_fmt = date_str.replace('-', '')  # YYYYMMDD

    zt_count = None
    dt_count = None

    try:
        df = ak.stock_zt_pool_em(date=date_fmt)
        zt_count = len(df) if df is not None and not df.empty else 0
    except Exception as e:
        logger.warning(f"[{date_str}] 涨停池拉取失败: {e}")

    try:
        df = ak.stock_zt_pool_dtgc_em(date=date_fmt)
        dt_count = len(df) if df is not None and not df.empty else 0
    except Exception as e:
        logger.warning(f"[{date_str}] 跌停池拉取失败: {e}")

    return zt_count, dt_count


def _fetch_north_flow(date_str: str) -> float | None:
    """拉取指定日期北向资金净流入（亿元），沪股通+深股通合并"""
    import akshare as ak
    import pandas as pd

    try:
        total = 0.0
        found = False
        for channel in ['沪股通', '深股通']:
            df = ak.stock_hsgt_hist_em(symbol=channel)
            if df is None or df.empty:
                continue
            df['日期'] = pd.to_datetime(df['日期'])
            row = df[df['日期'] == date_str]
            if not row.empty:
                val = row['当日成交净买额'].iloc[0]
                if pd.notna(val):
                    # 接口返回值单位是元，转换为亿元
                    total += float(val) / 1e8
                    found = True
        return round(total, 2) if found else None
    except Exception as e:
        logger.warning(f"[{date_str}] 北向资金拉取失败: {e}")
        return None


def _fetch_margin_balance(date_str: str) -> float | None:
    """拉取指定日期沪市两融余额（元）"""
    import akshare as ak

    try:
        date_fmt = date_str.replace('-', '')
        df = ak.stock_margin_sse(start_date=date_fmt, end_date=date_fmt)
        if df is None or df.empty:
            return None
        return float(df['融资融券余额'].iloc[0])
    except Exception as e:
        logger.warning(f"[{date_str}] 两融余额拉取失败: {e}")
        return None


def _upsert_row(conn, trade_date: str, zt: int | None, dt: int | None,
                north: float | None, margin: float | None):
    """写入或更新一条 market_sentiment 记录"""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO market_sentiment (trade_date, zt_count, dt_count, north_net_flow, margin_balance)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            zt_count = COALESCE(VALUES(zt_count), zt_count),
            dt_count = COALESCE(VALUES(dt_count), dt_count),
            north_net_flow = COALESCE(VALUES(north_net_flow), north_net_flow),
            margin_balance = COALESCE(VALUES(margin_balance), margin_balance)
    """, (trade_date, zt, dt, north, margin))
    conn.commit()


def fetch_one_day(conn, date_str: str):
    """采集并写入单日数据"""
    logger.info(f"采集 {date_str} ...")

    zt, dt = _fetch_zt_dt_count(date_str)
    north = _fetch_north_flow(date_str)
    margin = _fetch_margin_balance(date_str)

    # 如果所有字段都是 None（非交易日），跳过写入
    if zt is None and dt is None and north is None and margin is None:
        logger.debug(f"[{date_str}] 所有指标均为空（可能非交易日），跳过")
        return False

    _upsert_row(conn, date_str, zt, dt, north, margin)
    logger.info(f"[{date_str}] ✓ 涨停={zt} 跌停={dt} 北向={north}亿 两融={margin}")
    return True


def main():
    parser = argparse.ArgumentParser(description='市场情绪数据采集')
    parser.add_argument('--start', type=str, help='起始日期 YYYY-MM-DD')
    parser.add_argument('--end', type=str, help='结束日期 YYYY-MM-DD')
    args = parser.parse_args()

    from data.database import DatabaseManager
    db = DatabaseManager()
    conn = db._get_conn()

    try:
        if args.start and args.end:
            # 批量模式
            start = datetime.strptime(args.start, '%Y-%m-%d')
            end = datetime.strptime(args.end, '%Y-%m-%d')
            current = start
            success = 0
            total = 0
            while current <= end:
                date_str = current.strftime('%Y-%m-%d')
                # 跳过周末
                if current.weekday() < 5:
                    total += 1
                    if fetch_one_day(conn, date_str):
                        success += 1
                    # API 限流保护
                    time.sleep(0.5)
                current += timedelta(days=1)
            logger.info(f"批量采集完成: {success}/{total} 天写入成功")
        else:
            # 单日模式（当天）
            today = datetime.now().strftime('%Y-%m-%d')
            fetch_one_day(conn, today)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
