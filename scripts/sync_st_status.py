#!/usr/bin/env python3
"""
ST 标记根治脚本 — 从 baostock 逐股拉取历史逐日 isST 状态

原理：
  baostock 的 query_history_k_data_plus 接口在 adjustflag="3" 模式下
  可返回每日的 isST 字段（0/1），这是唯一能做到按日期精确标记的免费源。

流程：
  1. 从 MySQL 获取日期范围和所有唯一 symbol
  2. 先登录 baostock，登录失败时禁止清零历史 ST 标记
  3. 对每个 symbol 调用 baostock 获取 isST=1 的日期
  4. 全部查询成功后，用临时表刷新 daily_kline.is_st

预计耗时：~30-60 分钟（取决于网络和 baostock 限速）
"""

import argparse
import pymysql
import baostock as bs
import time
import sys
import socket

MYSQL_CONFIG = dict(
    host='localhost', user='root', password='root123456',
    database='quant_trading', charset='utf8mb4'
)

BAOSTOCK_TIMEOUT_SECONDS = 60
BAOSTOCK_LOGIN_RETRIES = 3
BAOSTOCK_LOGIN_RETRY_SLEEP_SECONDS = 10
BAOSTOCK_QUERY_RETRIES = 3
ST_STAGE_TABLE = "daily_kline_st_stage"
VALID_DATE_EXPR = "date REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'"


def get_bs_code(symbol: str) -> str:
    """转换 6 位代码为 baostock 格式"""
    if symbol.startswith('6'):
        return f"sh.{symbol}"
    elif symbol.startswith('0') or symbol.startswith('3'):
        return f"sz.{symbol}"
    return ""


def parse_args():
    parser = argparse.ArgumentParser(description="同步 daily_kline.is_st 标记")
    parser.add_argument("--from-date", dest="from_date", default=None,
                        help="增量模式：仅刷新 date > FROM_DATE 的 ST 标记")
    parser.add_argument("--to-date", dest="to_date", default=None,
                        help="增量/全量截止日期，默认 daily_kline 最大有效日期")
    return parser.parse_args()


def close_baostock_socket():
    """关闭 baostock 全局 socket，避免失败连接污染下一次重试"""
    try:
        import baostock.common.context as context

        sock = getattr(context, "default_socket", None)
        if sock is not None:
            sock.close()
        setattr(context, "default_socket", None)
    except Exception:
        pass


def login_baostock_with_retry():
    """登录 baostock；失败时重试，仍失败则退出"""
    socket.setdefaulttimeout(BAOSTOCK_TIMEOUT_SECONDS)

    for attempt in range(1, BAOSTOCK_LOGIN_RETRIES + 1):
        print(f"   尝试登录 {attempt}/{BAOSTOCK_LOGIN_RETRIES} ...")
        close_baostock_socket()
        lg = bs.login()
        if lg.error_code == '0':
            print("   ✅ 登录成功")
            return

        print(f"   ❌ 登录失败: {lg.error_code} {lg.error_msg}")
        if attempt < BAOSTOCK_LOGIN_RETRIES:
            print(f"   等待 {BAOSTOCK_LOGIN_RETRY_SLEEP_SECONDS}s 后重试")
            time.sleep(BAOSTOCK_LOGIN_RETRY_SLEEP_SECONDS)

    raise RuntimeError("baostock 登录失败，已中止；daily_kline.is_st 未被刷新")


def query_st_dates_with_retry(bs_code: str, date_min: str, date_max: str, after_date: str | None = None):
    """查询单只股票的 ST 日期；网络异常时重试"""
    last_error = None

    for attempt in range(1, BAOSTOCK_QUERY_RETRIES + 1):
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,isST",
                start_date=str(date_min),
                end_date=str(date_max),
                frequency="d",
                adjustflag="3"
            )

            if rs.error_code != '0':
                last_error = f"{rs.error_code} {rs.error_msg}"
                if attempt < BAOSTOCK_QUERY_RETRIES:
                    time.sleep(1)
                    continue
                return None, last_error

            st_dates = []
            while rs.error_code == '0' and rs.next():
                row = rs.get_row_data()
                if len(row) >= 2 and row[1] == '1' and (after_date is None or row[0] > after_date):
                    st_dates.append(row[0])
            return st_dates, None
        except Exception as e:
            last_error = str(e)
            close_baostock_socket()
            if attempt < BAOSTOCK_QUERY_RETRIES:
                login_baostock_with_retry()
                time.sleep(1)

    return None, last_error


def prepare_stage_table(cur):
    """准备 ST 临时结果表"""
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {ST_STAGE_TABLE} (
            symbol varchar(20) NOT NULL,
            date varchar(10) NOT NULL,
            PRIMARY KEY (symbol, date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cur.execute(f"TRUNCATE TABLE {ST_STAGE_TABLE}")


def apply_stage_to_daily_kline(cur, conn, from_date: str | None, to_date: str):
    """把 ST 临时结果应用到 daily_kline"""
    if from_date is None:
        cur.execute("UPDATE daily_kline SET is_st = 0 WHERE is_st = 1")
    else:
        cur.execute(
            f"UPDATE daily_kline SET is_st = 0 "
            f"WHERE is_st = 1 AND {VALID_DATE_EXPR} AND date > %s AND date <= %s",
            (from_date, to_date)
        )
    reset_rows = cur.rowcount
    if from_date is None:
        cur.execute(f"""
            UPDATE daily_kline dk
            JOIN {ST_STAGE_TABLE} s
              ON dk.symbol = s.symbol AND dk.date = s.date
            SET dk.is_st = 1
        """)
    else:
        cur.execute(f"""
            UPDATE daily_kline dk
            JOIN {ST_STAGE_TABLE} s
              ON dk.symbol = s.symbol AND dk.date = s.date
            SET dk.is_st = 1
            WHERE dk.date > %s AND dk.date <= %s
        """, (from_date, to_date))
    updated_rows = cur.rowcount
    conn.commit()
    return reset_rows, updated_rows


def main():
    args = parse_args()
    is_incremental = args.from_date is not None

    print("=" * 60)
    if is_incremental:
        print("  ST 标记增量同步：baostock 逐日 isST")
    else:
        print("  ST 标记根治：baostock 全量逐日 isST 同步")
    print("=" * 60)

    # 连接 MySQL
    conn = pymysql.connect(**MYSQL_CONFIG)
    cur = conn.cursor()

    # 步骤 0: 获取数据日期范围
    cur.execute(f"SELECT MIN(date), MAX(date), COUNT(*) FROM daily_kline WHERE {VALID_DATE_EXPR}")
    db_date_min, db_date_max, valid_rows = cur.fetchone()
    date_max = args.to_date or db_date_max
    date_min = args.from_date if is_incremental else db_date_min
    if not date_min or not date_max:
        print("\n❌ daily_kline 中没有有效日期数据，已中止")
        conn.close()
        sys.exit(1)
    if is_incremental and date_min >= date_max:
        print(f"\n✅ ST 已同步到 {date_max}，无新增日期需要处理")
        conn.close()
        return
    if is_incremental:
        print(f"\n📅 ST 增量范围: ({date_min}, {date_max}]")
    else:
        print(f"\n📅 daily_kline 日期范围: {date_min} ~ {date_max}")
    print(f"   有效日期行数: {valid_rows:,}")

    # 步骤 1: 获取所有唯一股票代码
    if is_incremental:
        cur.execute(
            f"SELECT DISTINCT symbol FROM daily_kline "
            f"WHERE {VALID_DATE_EXPR} AND date > %s AND date <= %s ORDER BY symbol",
            (args.from_date, date_max)
        )
    else:
        cur.execute(
            f"SELECT DISTINCT symbol FROM daily_kline "
            f"WHERE {VALID_DATE_EXPR} AND date <= %s ORDER BY symbol",
            (date_max,)
        )
    all_symbols = [r[0] for r in cur.fetchall()]
    if not all_symbols:
        print("\n✅ 指定日期范围内没有股票数据，跳过 ST 同步")
        conn.close()
        return
    print(f"\n📊 步骤1: 共 {len(all_symbols)} 只股票需要查询 isST")

    # 步骤 2: 登录 baostock。登录失败时不允许清零 is_st。
    print("\n🔌 步骤2: 登录 baostock ...")
    try:
        login_baostock_with_retry()
    except RuntimeError as e:
        print(f"   ❌ {e}")
        conn.close()
        sys.exit(1)

    # 步骤 3: 准备临时结果表
    print("\n🧾 步骤3: 准备 ST 临时结果表 ...")
    prepare_stage_table(cur)
    conn.commit()
    print(f"   ✅ 临时表 {ST_STAGE_TABLE} 已清空")

    # 步骤 4: 逐股查询 isST，先写入临时表，不直接改 daily_kline。
    print(f"\n🚀 步骤4: 逐股查询 isST (共 {len(all_symbols)} 只)...")
    t0 = time.time()
    total_st_rows = 0
    st_stock_count = 0
    errors = 0
    skipped = 0  # baostock 不支持的（如北交所）

    for i, sym in enumerate(all_symbols):
        bs_code = get_bs_code(sym)

        # 当前策略只需要沪深 A 股 ST 精确标记；北交所/B股等跳过。
        if not bs_code:
            skipped += 1
            continue

        st_dates, error = query_st_dates_with_retry(
            bs_code,
            date_min,
            date_max,
            after_date=args.from_date if is_incremental else None
        )
        if error is not None:
            errors += 1
            if errors <= 5:
                print(f"   ⚠️ {sym} 查询异常: {error}")
            continue

        if st_dates:
            st_stock_count += 1
            total_st_rows += len(st_dates)

            batch_size = 1000
            for j in range(0, len(st_dates), batch_size):
                batch = [(sym, d) for d in st_dates[j:j + batch_size]]
                cur.executemany(
                    f"INSERT IGNORE INTO {ST_STAGE_TABLE} (symbol, date) VALUES (%s, %s)",
                    batch
                )
            conn.commit()

        # 进度报告
        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            speed = (i + 1) / elapsed
            eta = (len(all_symbols) - i - 1) / speed / 60
            print(f"   ➤ 进度: {i+1}/{len(all_symbols)} "
                  f"| ST股: {st_stock_count} 只 / {total_st_rows} 行 "
                  f"| 错误: {errors} | 速度: {speed:.0f}只/s | ETA: {eta:.1f}min")

    bs.logout()
    elapsed_total = time.time() - t0

    if errors > 0:
        print(f"\n❌ 查询存在 {errors} 只股票错误，已中止应用；daily_kline.is_st 未被刷新")
        conn.close()
        sys.exit(1)

    # 步骤 5: 查询完整成功后才刷新 daily_kline.is_st。
    print("\n🔄 步骤5: 应用 ST 临时结果到 daily_kline ...")
    reset_rows, updated_rows = apply_stage_to_daily_kline(
        cur,
        conn,
        args.from_date if is_incremental else None,
        date_max
    )
    print(f"   ✅ 已清零旧标记 {reset_rows} 行，写入新标记 {updated_rows} 行")

    # 最终验证
    cur.execute("SELECT COUNT(*) FROM daily_kline WHERE is_st = 1")
    final_st = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT symbol) FROM daily_kline WHERE is_st = 1")
    final_st_stocks = cur.fetchone()[0]

    print(f"\n{'=' * 60}")
    print(f"  ✅ ST 标记根治完成！")
    print(f"  耗时: {elapsed_total/60:.1f} 分钟")
    print(f"  查询: {len(all_symbols)} 只 (跳过北交所 {skipped} 只, 错误 {errors} 只)")
    print(f"  结果: {final_st_stocks} 只股票被标记为 ST, 共 {final_st} 行")
    print(f"{'=' * 60}")

    conn.close()


if __name__ == "__main__":
    main()
