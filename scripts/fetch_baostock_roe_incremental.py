#!/usr/bin/env python3
"""
Baostock ROE 增量采集脚本（仅补缺）

与全量版 fetch_baostock_roe.py 的区别：
  - 先查询数据库已有的 stat_date 覆盖情况
  - 对于已满（>= 4500 条）的季度，直接跳过
  - 对于未满的季度，仅采集库中缺失的股票
  - 大幅减少网络请求数量（从 20000+ 降到几百~几千）

用法：
    python scripts/fetch_baostock_roe_incremental.py
"""
import sys
import time
from pathlib import Path
from datetime import date

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import baostock as bs
import pymysql

DB_HOST = "localhost"
DB_USER = "root"
DB_PASS = "root123456"
DB_NAME = "quant_trading"

# 每季度股票数达到此阈值则认为 "已满"，跳过
FULL_THRESHOLD = 4500

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
    if not val or val.strip() == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def get_target_quarters():
    """计算需要检查的最近 4 个季度"""
    now = date.today()
    quarters = []
    y, m = now.year, now.month

    # 当前季度的数据肯定还没出，从上一个季度开始
    if m <= 3:
        y, q = y - 1, 4
    elif m <= 6:
        y, q = y - 1, 4  # Q1 报告通常4月底才出齐
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


def quarter_to_stat_date(year, quarter):
    """季度转报告期日期"""
    mapping = {1: f"{year}-03-31", 2: f"{year}-06-30",
               3: f"{year}-09-30", 4: f"{year}-12-31"}
    return mapping[quarter]


def main():
    print("=" * 60)
    print("Baostock ROE 增量采集（仅补缺）")
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
    quarters_to_fetch = []

    print("\n季度覆盖情况：")
    for y, q in target_quarters:
        sd = quarter_to_stat_date(y, q)
        cnt = existing.get(sd, 0)
        status = "✅ 已满" if cnt >= FULL_THRESHOLD else f"⚠️ 不足（需补采）"
        print(f"  {y}年 Q{q} ({sd}): {cnt} 条 {status}")
        if cnt < FULL_THRESHOLD:
            quarters_to_fetch.append((y, q, sd, cnt))

    if not quarters_to_fetch:
        print("\n所有季度数据已完整，无需采集！")
        cursor.close()
        conn.close()
        return

    # 登录 Baostock
    print("\n登录 Baostock ...")
    lg = bs.login()
    if lg.error_code != '0':
        print(f"登录失败: {lg.error_msg}")
        return

    # 获取全 A 股代码
    all_codes = []
    rs = bs.query_stock_basic(code_name="", code="")
    while rs.next():
        row = rs.get_row_data()
        code = row[0]
        if (code.startswith('sh.6') or code.startswith('sz.0') or
                code.startswith('sz.3') or code.startswith('sh.68')):
            all_codes.append(code)
    print(f"全 A 股代码数: {len(all_codes)}")

    total_start = time.time()
    total_success = 0
    total_skip = 0

    for y, q, sd, existing_cnt in quarters_to_fetch:
        print(f"\n--- {y}年 Q{q} ({sd}): 库内已有 {existing_cnt} 条，补采中 ---")

        # 查询该季度已有的股票代码
        cursor.execute("SELECT symbol FROM baostock_profit WHERE stat_date = %s", (sd,))
        existing_symbols = {r[0] for r in cursor.fetchall()}

        # 仅采集缺失的股票
        codes_to_fetch = []
        for code in all_codes:
            symbol = code.split('.')[1]
            if symbol not in existing_symbols:
                codes_to_fetch.append(code)

        print(f"  需补采: {len(codes_to_fetch)} 只（跳过已有 {len(existing_symbols)} 只）")
        total_skip += len(existing_symbols)

        if not codes_to_fetch:
            continue

        batch = []
        batch_size = 200
        success = 0
        fail = 0
        t0 = time.time()

        for i, code in enumerate(codes_to_fetch):
            try:
                rs = bs.query_profit_data(code=code, year=y, quarter=q)
                if rs.error_code == '0':
                    while rs.next():
                        data = rs.get_row_data()
                        symbol = code.split('.')[1]
                        pub_date = data[1] if data[1] else None
                        stat_date = data[2] if data[2] else None
                        if not stat_date:
                            continue
                        batch.append((
                            symbol, pub_date, stat_date,
                            safe_float(data[3]), safe_float(data[4]),
                            safe_float(data[5]), safe_float(data[6]),
                            safe_float(data[7]), safe_float(data[9]),
                            safe_float(data[10]),
                        ))
                        success += 1
                else:
                    fail += 1
            except Exception:
                fail += 1

            if len(batch) >= batch_size:
                cursor.executemany(UPSERT_SQL, batch)
                conn.commit()
                batch = []

            if (i + 1) % 500 == 0 or (i + 1) == len(codes_to_fetch):
                elapsed = time.time() - t0
                speed = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (len(codes_to_fetch) - i - 1) / speed if speed > 0 else 0
                print(f"  [{i+1}/{len(codes_to_fetch)}] "
                      f"成功={success} 失败={fail} "
                      f"速度={speed:.0f}只/秒 ETA={eta:.0f}秒")

        if batch:
            cursor.executemany(UPSERT_SQL, batch)
            conn.commit()

        total_success += success

    bs.logout()
    elapsed_all = time.time() - total_start

    # 验证
    cursor.execute("SELECT COUNT(*) FROM baostock_profit")
    total_rows = cursor.fetchone()[0]

    print(f"\n{'=' * 60}")
    print(f"增量采集完成！")
    print(f"  新增: {total_success} 条")
    print(f"  跳过已有: {total_skip} 条")
    print(f"  耗时: {elapsed_all:.0f}秒 ({elapsed_all/60:.1f}分钟)")
    print(f"  表中总行数: {total_rows:,}")
    print(f"{'=' * 60}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
