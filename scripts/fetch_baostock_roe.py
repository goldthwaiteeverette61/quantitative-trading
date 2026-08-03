#!/usr/bin/env python3
"""
Baostock 季频盈利能力数据采集脚本
采集全 A 股的 ROE 等财务指标，存入 baostock_profit 表
用于与 PB/PE 近似 ROE 做对比验证

用法:
    python scripts/fetch_baostock_roe.py              # 采集最新可用季度
    python scripts/fetch_baostock_roe.py 2024 3        # 指定年份和季度
"""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import baostock as bs
import pymysql


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
    if not val or val.strip() == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def fetch_all_stock_codes():
    """获取全 A 股代码列表"""
    codes = []
    rs = bs.query_stock_basic(code_name="", code="")
    while rs.next():
        row = rs.get_row_data()
        code = row[0]
        # 只要沪深主板+创业板+科创板，排除基金、债券等
        if (code.startswith('sh.6') or code.startswith('sz.0') or
            code.startswith('sz.3') or code.startswith('sh.68')):
            codes.append(code)
    return codes


def fetch_profit_data(year, quarter, codes, cursor, conn):
    """批量采集指定季度的盈利能力数据"""
    total = len(codes)
    success = 0
    fail = 0
    batch = []
    batch_size = 200

    start = time.time()

    for i, code in enumerate(codes):
        try:
            rs = bs.query_profit_data(code=code, year=year, quarter=quarter)
            if rs.error_code == '0':
                while rs.next():
                    data = rs.get_row_data()
                    # data: [code, pubDate, statDate, roeAvg, npMargin, gpMargin,
                    #        netProfit, epsTTM, MBRevenue, totalShare, liqaShare]
                    symbol = code.split('.')[1]  # sh.600519 -> 600519
                    pub_date = data[1] if data[1] else None
                    stat_date = data[2] if data[2] else None

                    if not stat_date:
                        continue

                    batch.append((
                        symbol, pub_date, stat_date,
                        safe_float(data[3]),  # roeAvg
                        safe_float(data[4]),  # npMargin
                        safe_float(data[5]),  # gpMargin
                        safe_float(data[6]),  # netProfit
                        safe_float(data[7]),  # epsTTM
                        safe_float(data[9]),  # totalShare
                        safe_float(data[10]), # liqaShare
                    ))
                    success += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1

        # 批量写入
        if len(batch) >= batch_size:
            cursor.executemany(UPSERT_SQL, batch)
            conn.commit()
            batch = []

        # 进度报告
        if (i + 1) % 500 == 0 or (i + 1) == total:
            elapsed = time.time() - start
            speed = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (total - i - 1) / speed if speed > 0 else 0
            print(f"  [{i+1}/{total}] {(i+1)/total*100:.1f}%  "
                  f"成功={success}  失败={fail}  "
                  f"速度={speed:.0f}只/秒  ETA={eta:.0f}秒")

    # 剩余
    if batch:
        cursor.executemany(UPSERT_SQL, batch)
        conn.commit()

    return success, fail


def main():
    # 解析参数
    if len(sys.argv) >= 3:
        year = int(sys.argv[1])
        quarter = int(sys.argv[2])
    else:
        # 默认：采集最近多个季度
        import datetime
        now = datetime.date.today()
        year = now.year
        # 上一季度（当前季度数据可能还没出）
        month = now.month
        if month <= 3:
            year -= 1
            quarter = 3  # 采集去年Q3
        elif month <= 6:
            quarter = 4  # 采集去年Q4（年报季）
            year -= 1
        elif month <= 9:
            quarter = 1
        else:
            quarter = 2

    print("=" * 60)
    print(f"Baostock 季频盈利能力数据采集")
    print(f"目标: {year}年 Q{quarter}")
    print("=" * 60)

    # 登录 Baostock
    lg = bs.login()
    if lg.error_code != '0':
        print(f"[ERROR] Baostock 登录失败: {lg.error_msg}")
        return

    # 获取全A股代码
    codes = fetch_all_stock_codes()
    print(f"全A股代码数: {len(codes)}")

    # 连接数据库
    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4"
    )
    cursor = conn.cursor()

    # 采集多个季度
    quarters_to_fetch = []

    # 采集最近 4 个季度
    y, q = year, quarter
    for _ in range(4):
        quarters_to_fetch.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1

    total_success = 0
    total_fail = 0
    start_all = time.time()

    for y, q in quarters_to_fetch:
        print(f"\n--- {y}年 Q{q} ---")
        s, f = fetch_profit_data(y, q, codes, cursor, conn)
        total_success += s
        total_fail += f

    elapsed_all = time.time() - start_all

    # 验证
    cursor.execute("SELECT COUNT(*) FROM baostock_profit")
    total_rows = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT symbol) FROM baostock_profit")
    total_symbols = cursor.fetchone()[0]
    cursor.execute("SELECT MIN(stat_date), MAX(stat_date) FROM baostock_profit")
    date_range = cursor.fetchone()

    print(f"\n{'=' * 60}")
    print(f"采集完成!")
    print(f"  成功: {total_success}")
    print(f"  失败: {total_fail}")
    print(f"  耗时: {elapsed_all:.0f}秒 ({elapsed_all/60:.1f}分钟)")
    print(f"  表中总行数: {total_rows:,}")
    print(f"  股票数: {total_symbols}")
    print(f"  报告期范围: {date_range[0]} ~ {date_range[1]}")
    print(f"{'=' * 60}")

    cursor.close()
    conn.close()
    bs.logout()


if __name__ == "__main__":
    main()
