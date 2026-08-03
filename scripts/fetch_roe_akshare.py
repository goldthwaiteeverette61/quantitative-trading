#!/usr/bin/env python3
"""
AKShare ROE 增量采集脚本（极速版）

使用东财业绩报表批量接口 (stock_yjbb_em) 一次性获取全市场 ROE，
替代 Baostock 逐股串行的 40 分钟地狱。

全量采集 4 个季度: ~10 秒
增量补缺 1 个季度: ~2 秒

用法：
    python scripts/fetch_roe_akshare.py
"""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import akshare as ak
import pymysql
from datetime import date

DB_HOST = "localhost"
DB_USER = "root"
DB_PASS = "root123456"
DB_NAME = "quant_trading"


UPSERT_SQL = """
    INSERT INTO baostock_profit
    (symbol, pub_date, stat_date, roe_avg, np_margin, gp_margin,
     net_profit, eps_ttm, total_share, liq_share)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
        pub_date=VALUES(pub_date),
        roe_avg=VALUES(roe_avg), np_margin=VALUES(np_margin),
        gp_margin=VALUES(gp_margin), net_profit=VALUES(net_profit),
        eps_ttm=VALUES(eps_ttm), total_share=VALUES(total_share),
        liq_share=VALUES(liq_share)
"""


def safe_float(val):
    """安全解析浮点数"""
    if val is None:
        return None
    try:
        import math
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


def safe_decimal(val, max_abs):
    """按数据库 DECIMAL 字段范围过滤极端异常值"""
    v = safe_float(val)
    if v is None or abs(v) > max_abs:
        return None
    return v


def safe_date_str(val):
    """转成 MySQL DATE 可接受的 yyyy-mm-dd 字符串"""
    if val is None:
        return None
    text = str(val)[:10]
    if len(text) == 10 and text[4] == '-' and text[7] == '-':
        return text
    return None


def get_target_quarters():
    """计算需要检查的最近 4 个季度"""
    now = date.today()
    quarters = []
    y, m = now.year, now.month

    if m <= 3:
        y, q = y - 1, 4
    elif m <= 6:
        y, q = y - 1, 4
    elif m <= 9:
        y, q = y, 1
    else:
        y, q = y, 2

    for _ in range(4):
        quarters.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    return quarters


def quarter_to_date_str(year, quarter):
    """季度转 yyyymmdd 格式（AKShare 参数格式）"""
    mapping = {1: f"{year}0331", 2: f"{year}0630",
               3: f"{year}0930", 4: f"{year}1231"}
    return mapping[quarter]


def quarter_to_stat_date(year, quarter):
    """季度转 yyyy-mm-dd 格式（数据库存储格式）"""
    mapping = {1: f"{year}-03-31", 2: f"{year}-06-30",
               3: f"{year}-09-30", 4: f"{year}-12-31"}
    return mapping[quarter]


def fetch_quarter_data(year, quarter, cursor, conn, existing_symbols):
    """
    通过 AKShare 的 stock_yjbb_em 接口批量获取某季度全市场业绩报表
    返回新增入库的条数
    """
    date_str = quarter_to_date_str(year, quarter)
    stat_date = quarter_to_stat_date(year, quarter)

    try:
        df = ak.stock_yjbb_em(date=date_str)
    except Exception as e:
        print(f"  AKShare 接口调用失败: {e}")
        return 0

    if df is None or df.empty:
        print(f"  该季度暂无数据")
        return 0

    batch = []
    skipped = 0
    success = 0

    for _, row in df.iterrows():
        symbol = str(row.get('股票代码', '')).strip()
        if not symbol or len(symbol) != 6:
            continue

        # 跳过已有的
        if symbol in existing_symbols:
            skipped += 1
            continue

        pub_date = safe_date_str(row.get('最新公告日期'))
        roe = safe_decimal(row.get('净资产收益率'), 9999.999999)
        eps = safe_decimal(row.get('每股收益'), 9999.999999)
        np_margin = safe_decimal(row.get('销售毛利率'), 9999.999999)
        net_profit = safe_decimal(row.get('净利润-净利润'), 999999999999999999.99)

        batch.append((
            symbol, pub_date, stat_date,
            roe,        # roe_avg
            np_margin,  # np_margin (毛利率近似)
            np_margin,  # gp_margin
            net_profit, # net_profit
            eps,        # eps_ttm
            None,       # total_share (此接口不提供)
            None,       # liq_share
        ))
        success += 1

    if batch:
        cursor.executemany(UPSERT_SQL, batch)
        conn.commit()

    print(f"  新增 {success} 条，跳过已有 {skipped} 条")
    return success


def main():
    print("=" * 60)
    print("AKShare ROE 极速增量采集")
    print("=" * 60)

    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS,
                           database=DB_NAME, charset="utf8mb4")
    cursor = conn.cursor()

    # 查询各季度已有覆盖情况
    cursor.execute("""
        SELECT stat_date, COUNT(*) as cnt
        FROM baostock_profit
        GROUP BY stat_date
        ORDER BY stat_date DESC
    """)
    existing = {str(row[0]): row[1] for row in cursor.fetchall()}

    target_quarters = get_target_quarters()
    total_start = time.time()
    total_added = 0

    print("\n各季度现有覆盖：")
    for y, q in target_quarters:
        sd = quarter_to_stat_date(y, q)
        cnt = existing.get(sd, 0)
        print(f"  {y}年 Q{q} ({sd}): {cnt} 条")

    print()

    for y, q in target_quarters:
        sd = quarter_to_stat_date(y, q)
        cnt = existing.get(sd, 0)

        print(f"--- {y}年 Q{q} ({sd}): 库内 {cnt} 条，UPSERT 同步中 ---")

        # 获取该季度已有的股票代码
        cursor.execute("SELECT symbol FROM baostock_profit WHERE stat_date = %s", (sd,))
        existing_symbols = {r[0] for r in cursor.fetchall()}

        t0 = time.time()
        added = fetch_quarter_data(y, q, cursor, conn, existing_symbols)
        elapsed = time.time() - t0
        total_added += added
        print(f"  耗时: {elapsed:.1f} 秒")

    elapsed_all = time.time() - total_start

    # 最终验证
    cursor.execute("SELECT COUNT(*) FROM baostock_profit")
    total_rows = cursor.fetchone()[0]
    cursor.execute("""
        SELECT stat_date, COUNT(*) FROM baostock_profit
        GROUP BY stat_date ORDER BY stat_date DESC LIMIT 5
    """)
    print(f"\n{'=' * 60}")
    print(f"采集完成！")
    print(f"  新增: {total_added} 条")
    print(f"  总耗时: {elapsed_all:.1f} 秒")
    print(f"  表中总行数: {total_rows:,}")
    print(f"\n  各季度覆盖：")
    for row in cursor.fetchall():
        print(f"    {row[0]}: {row[1]} 条")
    print(f"{'=' * 60}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
