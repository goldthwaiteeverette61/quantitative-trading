import akshare as ak
import pandas as pd
import time
from loguru import logger
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.database import DatabaseManager

def main():
    db = DatabaseManager()

    # 获取最新的四季度作为基准
    year = 2024
    quarter = 4
    date_str = f"{year}1231"

    logger.info(f"⏳ 开始通过东方财富拉取 {date_str} 全市场资产负债表数据...")
    t0 = time.time()

    try:
        # 东方财富的报表接口是全市场 5000 多只一次性下发，极其高效！
        df_zcfz = ak.stock_zcfz_em(date=date_str)
        logger.info(f"成功获取全市场 {len(df_zcfz)} 条资产负债记录，正在计算权益乘数...")

        data_list = []
        for _, row in df_zcfz.iterrows():
            code = str(row['股票代码'])
            # 格式化符合内部标的规则 (000001.SZ / 600000.SH / 830000.BJ)
            if code.startswith(('60', '68')):
                symbol = f"{code}.SH"
            elif code.startswith(('00', '30')):
                symbol = f"{code}.SZ"
            elif code.startswith(('8', '4', '9')):
                symbol = f"{code}.BJ"
            else:
                continue

            total_asset = row['资产-总资产']
            total_equity = row['股东权益合计']
            stat_date = row['公告日期'] if pd.notna(row['公告日期']) else f"{year}-{quarter*3:02d}-30"

            # 计算权益乘数 ＝ 总资产 / 净资产(股东权益)
            try:
                if pd.notna(total_asset) and pd.notna(total_equity) and float(total_equity) != 0:
                    ae_val = float(total_asset) / float(total_equity)
                else:
                    ae_val = None
            except:
                ae_val = None

            if ae_val is not None:
                data_list.append({
                    "symbol": symbol,
                    "stat_date": stat_date,
                    "asset_to_equity": ae_val
                })

        df = pd.DataFrame(data_list)
        if df.empty:
            logger.error("解析结果为空！")
            return

        # 存入数据库
        conn = db._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS baostock_balance (
                        symbol VARCHAR(20),
                        stat_date DATE,
                        asset_to_equity FLOAT,
                        PRIMARY KEY (symbol)
                    )
                """)

                # 全量替换最新季度数据
                cursor.execute("TRUNCATE TABLE baostock_balance")

                insert_sql = """
                    INSERT INTO baostock_balance (symbol, stat_date, asset_to_equity)
                    VALUES (%s, %s, %s)
                """

                for _, r in df.iterrows():
                    cursor.execute(insert_sql, (r['symbol'], r['stat_date'], r['asset_to_equity']))
                conn.commit()
                logger.info(f"✅ 成功将 {len(df)} 条权益乘数数据写入 baostock_balance 库！耗时: {time.time()-t0:.2f}秒。")

        finally:
            conn.close()

    except Exception as e:
        logger.error(f"东方财富数据拉取或处理失败: {e}")

if __name__ == "__main__":
    main()
