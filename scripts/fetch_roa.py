#!/usr/bin/env python3
"""
东财 ROA 批量拉取 v2 — 简化版，串行+即时入库
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import akshare as ak
import time
import warnings
warnings.filterwarnings('ignore')
os.environ['PYTHONWARNINGS'] = 'ignore'

from data.database import DatabaseManager
from loguru import logger

db = DatabaseManager()
conn = db._get_conn()
cursor = conn.cursor()

# 建表
cursor.execute("""
CREATE TABLE IF NOT EXISTS eastmoney_roa (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    stat_date DATE NOT NULL,
    roa DECIMAL(10,4),
    roe DECIMAL(10,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_symbol_stat (symbol, stat_date),
    INDEX idx_symbol (symbol),
    INDEX idx_stat_date (stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
""")
conn.commit()

# 获取代码
cursor.execute("""
    SELECT DISTINCT symbol FROM stock_basic
    WHERE symbol REGEXP '^(000|001|002|003|300|600|601|603|605)'
    AND symbol IS NOT NULL
""")
codes = [r[0] for r in cursor.fetchall()]
total = len(codes)
logger.info(f"总计 {total} 只股票待拉取")

t0 = time.time()
total_rows = 0
fail_count = 0

for i, code in enumerate(codes):
    try:
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year='2019')
        rows = []
        for _, row in df.iterrows():
            sd = row['日期']
            roa = row.get('总资产利润率(%)')
            roe = row.get('净资产收益率(%)')
            if sd and roa is not None:
                rows.append((code, str(sd), float(roa), float(roe) if roe else None))

        if rows:
            cursor.executemany("""
                INSERT INTO eastmoney_roa (symbol, stat_date, roa, roe)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE roa=VALUES(roa), roe=VALUES(roe)
            """, rows)
            conn.commit()
            total_rows += len(rows)
    except Exception:
        fail_count += 1

    if (i + 1) % 100 == 0:
        elapsed = time.time() - t0
        speed = (i + 1) / elapsed
        eta = (total - i - 1) / speed / 60
        logger.info(
            f"[{i+1}/{total}] 入库 {total_rows} 条 | "
            f"失败 {fail_count} | {speed:.1f} 只/s | ETA {eta:.1f}m"
        )

elapsed = time.time() - t0

cursor.execute("SELECT COUNT(*) FROM eastmoney_roa")
db_cnt = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(DISTINCT symbol) FROM eastmoney_roa")
sym_cnt = cursor.fetchone()[0]
cursor.execute("SELECT MIN(stat_date), MAX(stat_date) FROM eastmoney_roa")
dr = cursor.fetchone()

logger.info("=" * 60)
logger.info(f"  ✅ 完成! 耗时 {elapsed/60:.1f}m")
logger.info(f"  入库: {db_cnt} 条 | 股票: {sym_cnt} 只")
logger.info(f"  时间: {dr[0]} ~ {dr[1]}")
logger.info(f"  失败: {fail_count}")
logger.info("=" * 60)
