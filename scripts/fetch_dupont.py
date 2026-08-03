#!/usr/bin/env python3
"""
从 Baostock Dupont（杜邦分析）表批量拉取 ROA 数据
=====================================================
数据来源：bs.query_dupont_data
核心字段：dupontROE / dupontAssetStoEquity → ROA = ROE / 权益乘数

存入新表 baostock_dupont，供回测引擎直接查询 ROA。
"""
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import baostock as bs
import pymysql
import time
from data.database import DatabaseManager
from loguru import logger

# ===== 建表 =====
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS baostock_dupont (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL COMMENT '股票代码（纯数字，如 000001）',
    pub_date DATE COMMENT '发布日期',
    stat_date DATE NOT NULL COMMENT '统计截止日期（如 2024-12-31）',
    dupont_roe DECIMAL(10,6) COMMENT '杜邦 ROE（小数形式，如 0.38 = 38%）',
    equity_multiplier DECIMAL(10,6) COMMENT '权益乘数',
    asset_turnover DECIMAL(10,6) COMMENT '总资产周转率',
    net_profit_to_income DECIMAL(10,6) COMMENT '净利润/利润总额',
    net_income_to_revenue DECIMAL(10,6) COMMENT '利润总额/营业收入',
    roa DECIMAL(10,6) COMMENT '总资产收益率 = ROE / 权益乘数（小数形式）',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_symbol_stat (symbol, stat_date),
    INDEX idx_symbol (symbol),
    INDEX idx_stat_date (stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Baostock 杜邦分析数据';
"""


def get_all_stock_codes():
    """从本地数据库获取所有股票代码"""
    db = DatabaseManager()
    conn = db._get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT symbol FROM stock_basic WHERE symbol IS NOT NULL")
    codes = [row[0] for row in cursor.fetchall()]
    logger.info(f"从 stock_basic 获取 {len(codes)} 只股票")
    return codes


def to_bs_code(symbol: str) -> str:
    """000001 → sz.000001"""
    s = str(symbol).zfill(6)
    prefix = 'sh' if s.startswith('6') else 'sz'
    return f'{prefix}.{s}'


def fetch_dupont_for_stock(code: str, years: list[int]) -> list[dict]:
    """拉取一只股票多年多季度的杜邦数据"""
    rows = []
    bs_code = to_bs_code(code)
    for year in years:
        for quarter in [1, 2, 3, 4]:
            try:
                rs = bs.query_dupont_data(code=bs_code, year=year, quarter=quarter)
                while rs.error_code == '0' and rs.next():
                    data = rs.get_row_data()
                    fields = rs.fields
                    d = dict(zip(fields, data))

                    roe = d.get('dupontROE', '')
                    equity_mult = d.get('dupontAssetStoEquity', '')
                    asset_turn = d.get('dupontAssetTurn', '')
                    pnitoni = d.get('dupontPnitoni', '')
                    nitogr = d.get('dupontNitogr', '')

                    # 计算 ROA
                    roa = None
                    if roe and equity_mult and float(equity_mult) != 0:
                        roa = float(roe) / float(equity_mult)

                    rows.append({
                        'symbol': code,
                        'pub_date': d.get('pubDate') or None,
                        'stat_date': d.get('statDate') or None,
                        'dupont_roe': float(roe) if roe else None,
                        'equity_multiplier': float(equity_mult) if equity_mult else None,
                        'asset_turnover': float(asset_turn) if asset_turn else None,
                        'net_profit_to_income': float(pnitoni) if pnitoni else None,
                        'net_income_to_revenue': float(nitogr) if nitogr else None,
                        'roa': roa,
                    })
            except Exception:
                pass
    return rows


def batch_insert(conn, rows: list[dict]):
    """批量插入，遇重复则更新"""
    if not rows:
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO baostock_dupont
        (symbol, pub_date, stat_date, dupont_roe, equity_multiplier, asset_turnover,
         net_profit_to_income, net_income_to_revenue, roa)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        dupont_roe = VALUES(dupont_roe),
        equity_multiplier = VALUES(equity_multiplier),
        asset_turnover = VALUES(asset_turnover),
        net_profit_to_income = VALUES(net_profit_to_income),
        net_income_to_revenue = VALUES(net_income_to_revenue),
        roa = VALUES(roa)
    """
    params = [
        (r['symbol'], r['pub_date'], r['stat_date'], r['dupont_roe'],
         r['equity_multiplier'], r['asset_turnover'],
         r['net_profit_to_income'], r['net_income_to_revenue'], r['roa'])
        for r in rows if r['stat_date']
    ]
    if params:
        cursor.executemany(sql, params)
        conn.commit()


def main():
    # 拉取年份范围：2019-2025（覆盖 5 年回测 + 余量）
    years = list(range(2019, 2026))

    logger.info("=" * 60)
    logger.info("  Baostock Dupont 数据拉取（ROA 补齐）")
    logger.info(f"  年份范围: {years[0]}-{years[-1]}")
    logger.info("=" * 60)

    # 建表
    db = DatabaseManager()
    conn = db._get_conn()
    cursor = conn.cursor()
    cursor.execute(CREATE_TABLE_SQL)
    conn.commit()
    logger.info("✅ baostock_dupont 表已就绪")

    # 获取代码列表
    codes = get_all_stock_codes()

    # 登录 baostock
    bs.login()

    total = len(codes)
    t0 = time.time()
    total_rows = 0
    batch_buffer = []
    BATCH_SIZE = 200  # 每 200 只入库一次

    for i, code in enumerate(codes):
        rows = fetch_dupont_for_stock(code, years)
        batch_buffer.extend(rows)
        total_rows += len(rows)

        # 每 200 只批量入库
        if (i + 1) % BATCH_SIZE == 0:
            batch_insert(conn, batch_buffer)
            batch_buffer = []
            elapsed = time.time() - t0
            speed = (i + 1) / elapsed
            eta = (total - i - 1) / speed / 60
            logger.info(
                f"[{i+1}/{total}] 已拉取 {total_rows} 条 | "
                f"速度 {speed:.1f} 只/s | 剩余 ~{eta:.1f} 分钟"
            )

        # 每 500 只 sleep 一下防限流
        if (i + 1) % 500 == 0:
            time.sleep(2)

    # 最后一批
    if batch_buffer:
        batch_insert(conn, batch_buffer)

    bs.logout()
    elapsed = time.time() - t0

    # 统计结果
    cursor.execute("SELECT COUNT(*) FROM baostock_dupont")
    db_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM baostock_dupont WHERE roa IS NOT NULL")
    roa_count = cursor.fetchone()[0]

    logger.info("=" * 60)
    logger.info(f"  ✅ 拉取完成！")
    logger.info(f"  总耗时: {elapsed/60:.1f} 分钟")
    logger.info(f"  入库总条数: {db_count}")
    logger.info(f"  有效 ROA 条数: {roa_count} ({roa_count/db_count*100:.1f}%)")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
