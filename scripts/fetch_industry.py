#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Baostock 全市场行业分类补全脚本（用于替代被反爬屏蔽的东财接口）
并将中证监分类映射包含 JK搅屎棍 策略所必须防守的关键词：
['银行', '煤炭', '石油石化', '公用事业', '通信']
"""

import sys
from pathlib import Path
import time

sys.path.append(str(Path(__file__).resolve().parent.parent))

from loguru import logger
import baostock as bs
import pandas as pd
from data.database import DatabaseManager

def enrich_industry_name(csrc_name: str) -> str:
    """给证监会分类名称打上东财防守板块的标签补丁"""
    if "货币金融服务" in csrc_name:
        return csrc_name + "_银行"
    if "煤炭开采" in csrc_name:
        return csrc_name + "_煤炭"
    if "石油" in csrc_name or "燃料加工" in csrc_name:
        return csrc_name + "_石油石化"
    if "电力" in csrc_name or "热力" in csrc_name or "燃气" in csrc_name or "水的生产" in csrc_name or "水利" in csrc_name:
        return csrc_name + "_公用事业"
    if "通信" in csrc_name or "电信" in csrc_name:
        return csrc_name + "_通信"

    return csrc_name

def main():
    logger.info("🚀 启动 Baostock 行业分类补全任务")

    lg = bs.login()
    if lg.error_code != '0':
        logger.error(f"Baostock 登录失败: {lg.error_msg}")
        return

    logger.info("正在查询全市场证监会行业分类...")
    rs = bs.query_stock_industry()
    if rs.error_code != '0':
        logger.error(f"查询行业分类失败: {rs.error_msg}")
        bs.logout()
        return

    data_list = []
    while (rs.error_code == '0') and rs.next():
        data_list.append(rs.get_row_data())

    bs.logout()

    if not data_list:
        logger.warning("Baostock 返回的数据为空！")
        return

    df = pd.DataFrame(data_list, columns=rs.fields)
    logger.info(f"✅ 成功获取 {len(df)} 条股票行业分类记录")

    # 构建入库数据
    records = []
    for _, row in df.iterrows():
        # baostock code looks like 'sh.600000', we want '600000'
        raw_code = str(row.get('code', ''))
        symbol = raw_code.split('.')[-1] if '.' in raw_code else raw_code

        if len(symbol) != 6 or not symbol.isdigit():
            continue

        industry = str(row.get('industry', ''))
        if not industry:
            continue

        enriched = enrich_industry_name(industry)
        records.append((symbol, enriched))

    if not records:
        logger.warning("解析后的有效更新数据为 0 条。")
        return

    # 入库
    db = DatabaseManager()
    conn = db._get_conn()
    total_updated = 0
    try:
        cursor = conn.cursor()
        sql = """
            INSERT INTO stock_basic (symbol, industry)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE industry=VALUES(industry)
        """
        cursor.executemany(sql, records)
        conn.commit()
        total_updated = len(records)
    except Exception as e:
        logger.error(f"更新数据库失败: {e}")
    finally:
        conn.close()

    logger.info(f"🎉 行业落地完毕！成功使用 Baostock 更新 {total_updated} 只股票的带标签行业分类。")

if __name__ == "__main__":
    main()
