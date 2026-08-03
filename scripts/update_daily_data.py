import sys
import os
import time
import random
from loguru import logger
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.universe import UniverseManager
from data.database import DatabaseManager

def main():
    logger.info("=========== 增量日度收盘数据更新开始 ===========")
    um = UniverseManager()
    db = DatabaseManager()

    # 每天只需获取最近 5 天的数据（防止节假日错漏）
    TARGET_DAYS = 5

    symbols = um.get_universe('all_a')
    total = len(symbols)
    logger.info(f"需要增量更新的股票总数: {total}")

    success, failed = 0, 0
    random.shuffle(symbols)

    import akshare as ak
    end_date = datetime.now()
    start_date = end_date - timedelta(days=TARGET_DAYS)
    start_str = start_date.strftime('%Y%m%d')
    end_str = end_date.strftime('%Y%m%d')

    for i, symbol in enumerate(symbols):
        if i % 100 == 0:
            logger.info(f"进度: {i}/{total} ...")

        code = symbol.split('.')[0]
        market = 'sh' if code.startswith('6') else 'sz'
        ak_symbol = f"{market}{code}"

        try:
            # 极速版不休眠太多
            time.sleep(0.01)
            df = ak.stock_zh_a_daily(symbol=ak_symbol, start_date=start_str, end_date=end_str, adjust='qfq')

            if not df.empty:
                if 'date' in df.columns: df.rename(columns={'date':'日期'}, inplace=True)
                if 'volume' in df.columns: df.rename(columns={'volume':'成交量'}, inplace=True)

                # save_klines 内部使用了 ON DUPLICATE KEY UPDATE，天然支持覆盖更新
                db.save_klines(code, df)
                success += 1
            else:
                failed += 1

        except Exception as e:
            # 忽略北交所等查询不到的小票
            failed += 1

    logger.success(f"=========== 增量更新完成! 成功: {success}, 失败: {failed} ===========")

if __name__ == '__main__':
    main()
