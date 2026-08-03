import sys
import time
import random
from unittest.mock import MagicMock
from loguru import logger

# 提前 Mock 掉 curl_cffi 绕过动态库加载报错
sys.modules['curl_cffi'] = MagicMock()
sys.modules['curl_cffi.requests'] = MagicMock()

import akshare as ak
from data.universe import UniverseManager
from data.database import DatabaseManager

def is_stock_downloaded(db: DatabaseManager, symbol: str) -> bool:
    """检查数据库中是否已存在该股票的充足数据（超过1000条视为已下载）"""
    code = symbol.split('.')[0]
    df = db.load_klines(code, days=1000)
    return df is not None and len(df) >= 800

def robust_download_pool(pool_name: str, years: int = 10, top_n: int = 500):
    um = UniverseManager()
    db = DatabaseManager()

    logger.info(f"=== 开始全量同步股票池: {pool_name} ===")

    # 这里 top_n 给得足够大，比如 500 就能获取全部中证 500 成分股
    symbols = um.get_universe(pool_name, top_n=top_n)

    import datetime
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=years * 365)
    start_str = start_date.strftime('%Y%m%d')
    end_str = end_date.strftime('%Y%m%d')

    success, skipped, failed = 0, 0, 0
    total = len(symbols)

    for i, symbol in enumerate(symbols):
        code = symbol.split('.')[0]

        # 1. 断点续传检查（防重复下载）
        if is_stock_downloaded(db, code):
            logger.info(f"[{i+1}/{total}] {symbol} 已存在足够数据，跳过。")
            skipped += 1
            continue

        market = 'sh' if code.startswith(('6')) else 'sz'
        ak_symbol = f"{market}{code}"

        # 2. 带有重试机制的请求
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 随机睡眠 2~5 秒防封禁
                sleep_time = random.uniform(2.0, 5.0)
                time.sleep(sleep_time)

                logger.info(f"[{i+1}/{total}] 正在下载 {symbol} ({attempt+1}/{max_retries}) ...")
                df = ak.stock_zh_a_daily(symbol=ak_symbol, start_date=start_str, end_date=end_str, adjust='qfq')

                if not df.empty:
                    db.save_klines(code, df)
                    logger.success(f"[{i+1}/{total}] {symbol} 保存成功！")
                    success += 1
                else:
                    logger.warning(f"[{i+1}/{total}] {symbol} 获取为空数据。")
                    failed += 1

                break  # 成功提取，跳出重试循环

            except Exception as e:
                err_str = str(e)
                logger.error(f"[{i+1}/{total}] {symbol} 第 {attempt+1} 次尝试失败: {err_str[:100]}")

                if attempt < max_retries - 1:
                    # 如果遇到类似"Connection aborted" 或 429 的严重封禁
                    penalty = 60 * (attempt + 1)  # 阶梯式惩罚休息 1分钟、2分钟...
                    logger.warning(f"可能触发限制，强制深度休眠 {penalty} 秒重启...")
                    time.sleep(penalty)
                else:
                    logger.error(f"[{i+1}/{total}] {symbol} 彻底失败，放弃当前股票。")
                    failed += 1

    logger.info(f"=== {pool_name} 批量同步完成 ===")
    logger.info(f"总计: {total} | 成功新增: {success} | 跳过已下载: {skipped} | 失败: {failed}")


if __name__ == "__main__":
    logger.info("启动夜间挂机全量下载模式 (防封禁版本)...")

    # 连续下载中证 500 和中证 1000
    try:
        # 下载整个中证 500 (500 只)
        robust_download_pool('csi500', years=10, top_n=500)

        logger.info("中证 500 完成，休息 5 分钟后开始中证 1000 ...")
        time.sleep(300)

        # 下载整个中证 1000 (1000 只)
        robust_download_pool('csi1000', years=10, top_n=1000)

        logger.success("所有夜间下载任务全部顺利完成！你可以随时使用了。")
    except KeyboardInterrupt:
        logger.warning("任务被用户手动中断！数据将保留当前进度，下次运行会自动跳过已下载部分。")
    except Exception as e:
        logger.error(f"发生未捕获异常: {e}")
