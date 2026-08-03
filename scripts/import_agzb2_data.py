#!/usr/bin/env python3
"""
从 /opt/quantitative-trading/datas/agzb2/ 追加导入增量日线数据到 MySQL daily_kline 表
- agzb2 结构：年/月/日.zip，每个 zip 内一个单日全市场 CSV
- 编码 UTF-8，列结构与 agzb 完全相同（26列）
- 使用 INSERT ... ON DUPLICATE KEY UPDATE 实现去重追加
"""
import os
import sys
import csv
import time
import math
import re
import glob
import zipfile
import io
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql

# ── 配置 ──────────────────────────────────────
DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "root"
DB_PASS = "root123456"
DB_NAME = "quant_trading"

DATA_DIR = PROJECT_ROOT / "datas" / "agzb2"
BATCH_SIZE = 5000


def parse_float(val):
    """安全解析浮点数"""
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
    """把 '20250627' 转成 '2025-06-27'"""
    raw = raw.strip()
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def symbol_from_code(code):
    """从 '000001.SZ' 提取 '000001' """
    return code.strip().split(".")[0]


UPSERT_SQL = """
    INSERT INTO daily_kline
    (symbol, date, open, close, high, low, volume, amount, outstanding_share, turnover,
     pre_close, change_amount, change_pct, turnover_free, volume_ratio,
     pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
     total_share, free_share, total_mv, circ_mv)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
        open=VALUES(open), close=VALUES(close), high=VALUES(high), low=VALUES(low),
        volume=VALUES(volume), amount=VALUES(amount),
        outstanding_share=VALUES(outstanding_share), turnover=VALUES(turnover),
        pre_close=VALUES(pre_close), change_amount=VALUES(change_amount),
        change_pct=VALUES(change_pct), turnover_free=VALUES(turnover_free),
        volume_ratio=VALUES(volume_ratio),
        pe=VALUES(pe), pe_ttm=VALUES(pe_ttm), pb=VALUES(pb),
        ps=VALUES(ps), ps_ttm=VALUES(ps_ttm),
        dv_ratio=VALUES(dv_ratio), dv_ttm=VALUES(dv_ttm),
        total_share=VALUES(total_share), free_share=VALUES(free_share),
        total_mv=VALUES(total_mv), circ_mv=VALUES(circ_mv)
"""


def parse_row(row):
    """解析一行 CSV 数据，返回元组"""
    if len(row) < 26:
        return None

    symbol = symbol_from_code(row[0])
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

    return (
        symbol, date_str,
        open_p or 0, close_p or 0, high_p or 0, low_p or 0,
        volume, amount, outstanding_share_val, turnover_val or 0,
        pre_close, change_amount, change_pct,
        turnover_free, volume_ratio,
        pe, pe_ttm, pb, ps, ps_ttm,
        dv_ratio, dv_ttm,
        total_share_val, free_share_val,
        total_mv_val, circ_mv_val
    )


def process_zip(zip_path, cursor, conn, stats):
    """处理单个 zip 文件"""
    rows = []
    line_count = 0

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            csv_names = [n for n in zf.namelist() if n.endswith('.csv')]
            if not csv_names:
                print(f"  [WARN] {zip_path.name}: zip 内无 CSV 文件")
                return

            for csv_name in csv_names:
                with zf.open(csv_name) as f:
                    raw = f.read()
                    # 尝试 UTF-8，fallback 到 GBK
                    try:
                        text = raw.decode('utf-8')
                    except UnicodeDecodeError:
                        text = raw.decode('gbk')

                    reader = csv.reader(io.StringIO(text))
                    next(reader)  # 跳过表头

                    for row in reader:
                        parsed = parse_row(row)
                        if parsed is None:
                            continue
                        line_count += 1
                        rows.append(parsed)

                        if len(rows) >= BATCH_SIZE:
                            cursor.executemany(UPSERT_SQL, rows)
                            stats["rows"] += len(rows)
                            rows = []

        # 剩余行
        if rows:
            cursor.executemany(UPSERT_SQL, rows)
            stats["rows"] += len(rows)

        conn.commit()
        stats["files"] += 1

    except Exception as e:
        print(f"  [ERROR] {zip_path.name}: {e}")
        stats["errors"] += 1


def _extract_date_from_zip(zip_path: Path) -> str:
    """从 zip 文件名中提取日期字符串，如 '2026-04-13'"""
    stem = zip_path.stem
    # 处理带 (1) 后缀的重复文件：'2026-04-13 (1)' -> '2026-04-13'
    clean = re.sub(r'\s*\(.*\)', '', stem).strip()
    return clean


def main():
    parser = argparse.ArgumentParser(description='A股增量数据追加导入 (agzb2)')
    parser.add_argument('legacy_dir', nargs='?', default=None,
                        help='[兼容旧调用] 指定数据目录（已废弃，建议使用 --after-date）')
    parser.add_argument('--after-date', dest='after_date', default=None,
                        help='仅导入日期 > 此值的 zip 文件（格式：2026-04-10）')
    args = parser.parse_args()

    data_dir = Path(args.legacy_dir) if args.legacy_dir else DATA_DIR

    print("=" * 60)
    print("A股增量数据追加导入 (agzb2)")
    print(f"数据目录: {data_dir}")
    if args.after_date:
        print(f"增量模式: 仅导入日期 > {args.after_date}")
    print("=" * 60)

    # 收集所有 zip 文件
    zip_files = sorted(Path(p) for p in glob.glob(str(data_dir / "**" / "*.zip"), recursive=True))

    # 按日期过滤（增量模式）
    if args.after_date:
        before = len(zip_files)
        filtered = []
        for zp in zip_files:
            zdate = _extract_date_from_zip(zp)
            if re.match(r'^\d{4}-\d{2}-\d{2}$', zdate) and zdate > args.after_date:
                filtered.append(zp)
        zip_files = filtered
        print(f"全量 {before} 个 zip，筛选后 {len(zip_files)} 个需要导入")

    total_files = len(zip_files)
    if total_files > 0:
        print(f"日期范围: {_extract_date_from_zip(zip_files[0])} ~ {_extract_date_from_zip(zip_files[-1])}\n")

    if total_files == 0:
        print("[INFO] 无新增数据需要导入")
        return

    # 查询现有数据
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
        autocommit=False
    )
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM daily_kline")
    before_count = cursor.fetchone()[0]
    print(f"[INFO] 追加前 daily_kline 总行数: {before_count:,}\n")

    # 批量导入
    stats = {"files": 0, "rows": 0, "errors": 0}
    start_time = time.time()

    for i, zip_file in enumerate(zip_files):
        process_zip(zip_file, cursor, conn, stats)

        if (i + 1) % 10 == 0 or (i + 1) == total_files:
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

    # 查询追加后数据
    cursor.execute("SELECT COUNT(*) FROM daily_kline")
    after_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT symbol) FROM daily_kline")
    after_symbols = cursor.fetchone()[0]
    cursor.execute("SELECT MIN(date), MAX(date) FROM daily_kline")
    date_range = cursor.fetchone()

    print(f"\n{'=' * 60}")
    print(f"追加导入完成!")
    print(f"  zip 文件: {stats['files']}/{total_files}")
    print(f"  处理行数: {stats['rows']:,}")
    print(f"  错误: {stats['errors']}")
    print(f"  耗时: {elapsed:.1f} 秒")
    print(f"{'=' * 60}")
    print(f"\n[验证] daily_kline 表:")
    print(f"  追加前: {before_count:,} 行")
    print(f"  追加后: {after_count:,} 行 (新增 {after_count - before_count:,})")
    print(f"  股票数: {after_symbols}")
    print(f"  日期范围: {date_range[0]} ~ {date_range[1]}")
    print(f"\n[DONE] 全部完成!")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
