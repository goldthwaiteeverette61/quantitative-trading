"""
从 akshare 动态拉取最新的申万一级行业分类（2021标准），写入 MySQL 数据库。
完全淘汰硬编码的旧申万字典，避免由于诸如“采掘”被拆分导致的部分代码报错。
"""
import akshare as ak
import time
import sys
import os

# 确保能找到 data.database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.database import DatabaseManager

def main():
    db = DatabaseManager()
    conn = db._get_conn()
    cursor = conn.cursor()

    # 创建申万行业映射表（MySQL 语法）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sw1_industry (
            stock_code VARCHAR(10) PRIMARY KEY,
            stock_name VARCHAR(50),
            sw1_code VARCHAR(20),
            sw1_name VARCHAR(50),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    print("Fetching current active SW1 Indices...")
    df_index = ak.sw_index_first_info()
    if df_index is None or df_index.empty:
        print("Failed to fetch SW1 index list.")
        return

    total = 0
    for _, idx_row in df_index.iterrows():
        # 拆解如 "801010.SI" 为 "801010"
        code = idx_row['行业代码'].split('.')[0]
        name = idx_row['行业名称']
        print(f"正在拉取 {code} ({name})...")
        try:
            df = ak.index_component_sw(symbol=code)
            if df is None or df.empty:
                print(f"  ⚠️ {code} 无成分股数据")
                continue

            for _, row in df.iterrows():
                stock_code = str(row['证券代码']).zfill(6)
                stock_name = row['证券名称']
                # MySQL的UPSERT语法
                cursor.execute("""
                    INSERT INTO sw1_industry (stock_code, stock_name, sw1_code, sw1_name)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        stock_name=VALUES(stock_name),
                        sw1_code=VALUES(sw1_code),
                        sw1_name=VALUES(sw1_name)
                """, (stock_code, stock_name, code, name))
                total += 1

            conn.commit()
            print(f"  ✅ {len(df)} 只股票已入库")
            time.sleep(0.5)  # 控制请求频率
        except Exception as e:
            print(f"  ❌ 拉取失败: {e}")

    print(f"==== 申万数据更新完成，共覆盖 {total} 条最新映射记录 ====")

if __name__ == '__main__':
    main()
