#!/usr/bin/env python3
"""
从 /opt/quantitative-trading/datas/agzb/ 导入 A 股历史 CSV 数据到 MySQL
- 创建 daily_kline_new 表（原有字段 + 全部 CSV 附加字段）
- 批量导入全部 CSV
- 原子切换表名：daily_kline -> daily_kline_bak_xxx, daily_kline_new -> daily_kline
"""
import os
import sys
import csv
import time
import math
from datetime import datetime
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql

# ── 配置 ──────────────────────────────────────
DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "root"
DB_PASS = "root123456"
DB_NAME = "quant_trading"
DATA_DIR = PROJECT_ROOT / "datas" / "agzb"
BATCH_SIZE = 5000  # 每批插入行数

# ── CSV 列索引映射 ────────────────────────────
# 0: 证券代码         -> symbol (从文件名取，去后缀)
# 1: 交易时间         -> date
# 2: 开盘价           -> open
# 3: 最高价           -> high
# 4: 最低价           -> low
# 5: 收盘价           -> close
# 6: 前收盘价         -> pre_close
# 7: 涨跌额           -> change_amount
# 8: 涨跌幅           -> change_pct
# 9: 成交量(手)       -> volume (转为股: *100)
# 10: 成交额(千元)    -> amount (转为元: *1000)
# 11: 换手率（%）     -> turnover
# 12: 换手率（自由流通股）-> turnover_free
# 13: 量比            -> volume_ratio
# 14: 市盈率          -> pe
# 15: 市盈率TTM       -> pe_ttm
# 16: 市净率          -> pb
# 17: 市销率          -> ps
# 18: 市销率TTM       -> ps_ttm
# 19: 股息率(%)       -> dv_ratio
# 20: 股息率TTM(%)    -> dv_ttm
# 21: 总股本(万股)    -> total_share (转为股: *10000)
# 22: 流通股本(万股)  -> outstanding_share (转为股: *10000)
# 23: 自由流通股本(万)-> free_share (转为股: *10000)
# 24: 总市值(万元)    -> total_mv (转为元: *10000)
# 25: 流通市值(万元)  -> circ_mv (转为元: *10000)


def parse_float(val):
    """安全解析浮点数，nan/空值返回 None"""
    if not val or val.strip() == "" or val.strip().lower() == "nan":
        return None
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (ValueError, TypeError):
        return None


def format_date(raw):
    """把 '19910403' 转成 '1991-04-03'"""
    raw = raw.strip()
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def symbol_from_filename(filename):
    """从 '000001.SZ.csv' 提取 '000001' """
    return filename.split(".")[0]


def create_new_table(cursor):
    """创建 daily_kline_new 表"""
    cursor.execute("DROP TABLE IF EXISTS daily_kline_new")
    cursor.execute("""
        CREATE TABLE daily_kline_new (
            symbol       VARCHAR(20)  NOT NULL,
            date         VARCHAR(10)  NOT NULL,
            open         DOUBLE       DEFAULT 0,
            close        DOUBLE       DEFAULT 0,
            high         DOUBLE       DEFAULT 0,
            low          DOUBLE       DEFAULT 0,
            volume       DOUBLE       DEFAULT 0,
            amount       DOUBLE       DEFAULT 0,
            outstanding_share DOUBLE  DEFAULT 0,
            turnover     DOUBLE       DEFAULT 0,
            -- 以下为新增附加字段 --
            pre_close    DOUBLE       DEFAULT NULL,
            change_amount DOUBLE      DEFAULT NULL,
            change_pct   DOUBLE       DEFAULT NULL,
            turnover_free DOUBLE      DEFAULT NULL,
            volume_ratio DOUBLE       DEFAULT NULL,
            pe           DOUBLE       DEFAULT NULL,
            pe_ttm       DOUBLE       DEFAULT NULL,
            pb           DOUBLE       DEFAULT NULL,
            ps           DOUBLE       DEFAULT NULL,
            ps_ttm       DOUBLE       DEFAULT NULL,
            dv_ratio     DOUBLE       DEFAULT NULL,
            dv_ttm       DOUBLE       DEFAULT NULL,
            total_share  DOUBLE       DEFAULT NULL,
            free_share   DOUBLE       DEFAULT NULL,
            total_mv     DOUBLE       DEFAULT NULL,
            circ_mv      DOUBLE       DEFAULT NULL,
            PRIMARY KEY (symbol, date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # 添加常用索引
    cursor.execute("CREATE INDEX idx_dkn_date ON daily_kline_new (date)")
    cursor.execute("CREATE INDEX idx_dkn_symbol ON daily_kline_new (symbol)")
    print("[OK] daily_kline_new 表已创建")


def process_csv(filepath, symbol, cursor, conn, stats):
    """处理单个 CSV 文件"""
    rows = []
    line_count = 0

    try:
        with open(filepath, "r", encoding="gbk") as f:
            reader = csv.reader(f)
            next(reader)  # 跳过表头
            for row in reader:
                if len(row) < 26:
                    continue
                line_count += 1

                date_str = format_date(row[1])
                open_p = parse_float(row[2])
                high_p = parse_float(row[3])
                low_p = parse_float(row[4])
                close_p = parse_float(row[5])
                pre_close = parse_float(row[6])
                change_amount = parse_float(row[7])
                change_pct = parse_float(row[8])

                # 成交量: 手 -> 股 (*100)
                vol_raw = parse_float(row[9])
                volume = vol_raw * 100 if vol_raw is not None else 0

                # 成交额: 千元 -> 元 (*1000)
                amt_raw = parse_float(row[10])
                amount = amt_raw * 1000 if amt_raw is not None else 0

                turnover_val = parse_float(row[11])
                turnover_free = parse_float(row[12])
                volume_ratio = parse_float(row[13])
                pe = parse_float(row[14])
                pe_ttm = parse_float(row[15])
                pb = parse_float(row[16])
                ps = parse_float(row[17])
                ps_ttm = parse_float(row[18])
                dv_ratio = parse_float(row[19])
                dv_ttm = parse_float(row[20])

                # 总股本: 万股 -> 股 (*10000)
                ts_raw = parse_float(row[21])
                total_share_val = ts_raw * 10000 if ts_raw is not None else None

                # 流通股本: 万股 -> 股 (*10000)
                os_raw = parse_float(row[22])
                outstanding_share_val = os_raw * 10000 if os_raw is not None else 0

                # 自由流通股本: 万 -> 股 (*10000)
                fs_raw = parse_float(row[23])
                free_share_val = fs_raw * 10000 if fs_raw is not None else None

                # 总市值: 万元 -> 元 (*10000)
                tmv_raw = parse_float(row[24])
                total_mv_val = tmv_raw * 10000 if tmv_raw is not None else None

                # 流通市值: 万元 -> 元 (*10000)
                cmv_raw = parse_float(row[25])
                circ_mv_val = cmv_raw * 10000 if cmv_raw is not None else None

                rows.append((
                    symbol, date_str,
                    open_p or 0, close_p or 0, high_p or 0, low_p or 0,
                    volume, amount, outstanding_share_val, turnover_val or 0,
                    pre_close, change_amount, change_pct,
                    turnover_free, volume_ratio,
                    pe, pe_ttm, pb, ps, ps_ttm,
                    dv_ratio, dv_ttm,
                    total_share_val, free_share_val,
                    total_mv_val, circ_mv_val
                ))

                # 批量插入
                if len(rows) >= BATCH_SIZE:
                    insert_batch(cursor, rows)
                    stats["rows"] += len(rows)
                    rows = []

        # 剩余行
        if rows:
            insert_batch(cursor, rows)
            stats["rows"] += len(rows)

        conn.commit()
        stats["files"] += 1
        stats["file_rows"] = line_count

    except Exception as e:
        print(f"  [ERROR] {filepath.name}: {e}")
        stats["errors"] += 1


INSERT_SQL = """
    INSERT INTO daily_kline_new
    (symbol, date, open, close, high, low, volume, amount, outstanding_share, turnover,
     pre_close, change_amount, change_pct, turnover_free, volume_ratio,
     pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
     total_share, free_share, total_mv, circ_mv)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


def insert_batch(cursor, rows):
    """批量插入"""
    cursor.executemany(INSERT_SQL, rows)


def atomic_swap(cursor, conn):
    """原子表名切换"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    bak_name = f"daily_kline_bak_{timestamp}"

    print(f"\n[SWAP] 开始表名切换...")
    print(f"  daily_kline -> {bak_name}")
    print(f"  daily_kline_new -> daily_kline")

    cursor.execute(f"RENAME TABLE daily_kline TO `{bak_name}`, daily_kline_new TO daily_kline")
    conn.commit()
    print(f"[OK] 表名切换完成！旧表备份为: {bak_name}")
    return bak_name


def main():
    print("=" * 60)
    print("A股历史数据导入工具")
    print(f"数据目录: {DATA_DIR}")
    print("=" * 60)

    # 收集 CSV 文件
    csv_files = sorted([f for f in DATA_DIR.iterdir() if f.suffix == ".csv"])
    total_files = len(csv_files)
    print(f"发现 {total_files} 个 CSV 文件\n")

    if total_files == 0:
        print("[ERROR] 未找到 CSV 文件!")
        return

    # 连接数据库
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
        autocommit=False
    )
    cursor = conn.cursor()
    print("[OK] 数据库连接成功\n")

    # 1. 创建新表
    create_new_table(cursor)
    conn.commit()

    # 2. 批量导入
    stats = {"files": 0, "rows": 0, "errors": 0, "file_rows": 0}
    start_time = time.time()

    for i, csv_file in enumerate(csv_files):
        symbol = symbol_from_filename(csv_file.name)
        process_csv(csv_file, symbol, cursor, conn, stats)

        # 每 100 个文件报告一次进度
        if (i + 1) % 100 == 0 or (i + 1) == total_files:
            elapsed = time.time() - start_time
            speed = stats["rows"] / elapsed if elapsed > 0 else 0
            pct = (i + 1) / total_files * 100
            eta = (elapsed / (i + 1)) * (total_files - i - 1) if i > 0 else 0
            print(
                f"  [{i+1}/{total_files}] {pct:.1f}%  "
                f"累计 {stats['rows']:,} 行  "
                f"速度 {speed:,.0f} 行/秒  "
                f"ETA {eta:.0f}s  "
                f"错误 {stats['errors']}"
            )

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"导入完成!")
    print(f"  文件: {stats['files']}/{total_files}")
    print(f"  总行数: {stats['rows']:,}")
    print(f"  错误: {stats['errors']}")
    print(f"  耗时: {elapsed:.1f} 秒")
    print(f"{'=' * 60}")

    # 3. 原子切换表名
    if stats["errors"] == 0:
        bak_name = atomic_swap(cursor, conn)
        # 验证
        cursor.execute("SELECT COUNT(*) FROM daily_kline")
        new_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT symbol) FROM daily_kline")
        new_symbols = cursor.fetchone()[0]
        print(f"\n[验证] 新 daily_kline 表: {new_count:,} 行, {new_symbols} 只股票")
    else:
        print(f"\n[WARNING] 存在 {stats['errors']} 个错误，跳过表名切换。")
        print("请检查错误后手动执行:")
        print("  RENAME TABLE daily_kline TO daily_kline_bak_xxx, daily_kline_new TO daily_kline;")

    cursor.close()
    conn.close()
    print("\n[DONE] 全部完成!")


if __name__ == "__main__":
    main()
