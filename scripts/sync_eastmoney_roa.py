#!/usr/bin/env python3
"""
AKShare 同步东财报告期 ROE/ROA 到 eastmoney_roa 表（增量补缺）

使用东财批量接口：
  - stock_yjbb_em: 业绩报表 → ROE（净资产收益率）
  - stock_lrb_em:  利润表   → 净利润
  - stock_zcfz_em: 资产负债表 → 总资产
  - ROA = 净利润 / 总资产 × 100

全量采集 4 个季度: ~10 秒
"""
import sys
import time
import math
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
    INSERT INTO eastmoney_roa (symbol, stat_date, roe, roa)
    VALUES (%s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        roe = VALUES(roe),
        roa = VALUES(roa)
"""


def safe_float(val):
    if val is None:
        return None
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


def safe_decimal(val, max_abs):
    v = safe_float(val)
    if v is None or abs(v) > max_abs:
        return None
    return v


def quarter_to_date_str(year, quarter):
    mapping = {1: f"{year}0331", 2: f"{year}0630",
               3: f"{year}0930", 4: f"{year}1231"}
    return mapping[quarter]


def quarter_to_stat_date(year, quarter):
    mapping = {1: f"{year}-03-31", 2: f"{year}-06-30",
               3: f"{year}-09-30", 4: f"{year}-12-31"}
    return mapping[quarter]


def get_target_quarters():
    """计算最近 4 个季度"""
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


def fetch_quarter(year, quarter, cursor, conn, existing_symbols):
    """通过 AKShare 批量获取某季度的 ROE 和 ROA，写入 eastmoney_roa"""
    date_str = quarter_to_date_str(year, quarter)
    stat_date = quarter_to_stat_date(year, quarter)

    # 获取业绩报表（ROE）
    try:
        yjbb = ak.stock_yjbb_em(date=date_str)
    except Exception as e:
        print(f"  stock_yjbb_em 失败: {e}")
        return 0

    if yjbb is None or yjbb.empty:
        print(f"  该季度暂无业绩报表数据")
        return 0

    # 获取利润表和资产负债表（用于计算 ROA）
    roa_map = {}
    try:
        lrb = ak.stock_lrb_em(date=date_str)
        zcfz = ak.stock_zcfz_em(date=date_str)
        if lrb is not None and zcfz is not None and not lrb.empty and not zcfz.empty:
            # 合并计算 ROA
            merged = lrb[['股票代码', '净利润']].merge(
                zcfz[['股票代码', '资产-总资产']], on='股票代码', how='inner'
            )
            for _, row in merged.iterrows():
                np_val = safe_float(row.get('净利润'))
                ta_val = safe_float(row.get('资产-总资产'))
                if np_val is not None and ta_val is not None and ta_val > 0:
                    roa = round(np_val / ta_val * 100, 4)
                    if abs(roa) <= 999999.9999:
                        roa_map[str(row['股票代码']).strip()] = roa
    except Exception as e:
        print(f"  利润表/资产负债表获取失败（ROA 将部分缺失）: {e}")

    batch = []
    skipped = 0
    success = 0

    for _, row in yjbb.iterrows():
        symbol = str(row.get('股票代码', '')).strip()
        if not symbol or len(symbol) != 6:
            continue

        if symbol in existing_symbols:
            skipped += 1
            continue

        roe = safe_decimal(row.get('净资产收益率'), 999999.9999)
        roa = roa_map.get(symbol)

        if roe is None:
            continue

        batch.append((symbol, stat_date, roe, roa))
        success += 1

    if batch:
        cursor.executemany(UPSERT_SQL, batch)
        conn.commit()

    print(f"  新增 {success} 条，跳过已有 {skipped} 条，ROA 覆盖 {sum(1 for b in batch if b[3] is not None)}/{success}")
    return success


def main():
    print("=" * 60)
    print("AKShare 东财报告期 ROE/ROA 增量同步")
    print("=" * 60)

    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS,
                           database=DB_NAME, charset="utf8mb4")
    cursor = conn.cursor()

    # 查询各季度覆盖
    cursor.execute("""
        SELECT stat_date, COUNT(*) FROM eastmoney_roa
        GROUP BY stat_date ORDER BY stat_date DESC
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

        cursor.execute("SELECT symbol FROM eastmoney_roa WHERE stat_date = %s", (sd,))
        existing_symbols = {r[0] for r in cursor.fetchall()}

        t0 = time.time()
        added = fetch_quarter(y, q, cursor, conn, existing_symbols)
        elapsed = time.time() - t0
        total_added += added
        print(f"  耗时: {elapsed:.1f} 秒")

    elapsed_all = time.time() - total_start

    # 最终验证
    cursor.execute("SELECT COUNT(*) FROM eastmoney_roa")
    total_rows = cursor.fetchone()[0]
    cursor.execute("""
        SELECT stat_date, COUNT(*) FROM eastmoney_roa
        GROUP BY stat_date ORDER BY stat_date DESC LIMIT 5
    """)
    print(f"\n{'=' * 60}")
    print(f"同步完成！")
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
