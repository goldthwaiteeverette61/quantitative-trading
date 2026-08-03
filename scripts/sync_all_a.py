import sys
import os
import time
import random
from loguru import logger

# 添加项目根目录到 sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.universe import UniverseManager
from data.database import DatabaseManager
from data.fetcher import DataFetcher

def main():
    logger.info("============== 开始执行全A股数据补齐任务 ==============")
    um = UniverseManager()
    db = DatabaseManager()
    fetcher = DataFetcher()

    # ====== 注入代理配置 ======
    # 强制让 DataFetcher 内部的 session 走本地代理，由于东方财富可能封禁了当前真实公网 IP
    proxy_url = "http://127.0.0.1:7897"
    logger.info(f"🔑 启用本地代理: {proxy_url} (绕过IP封禁)")

    from data.fetcher import _session
    _session.proxies = {
        'http': proxy_url,
        'https': proxy_url
    }

    # 因为东方财富通常在代理的分流规则中被设为 DIRECT (直连)
    # 如果代理客户端没有开启“全局代理(Global)”，即使设了代理依然会走原 IP
    logger.warning("请确保你的代理客户端（如 Clash / V2Ray）已开启【全局路由 / Global Mode】！否则东方财富请求还是会直连。")
    # ==========================

    # 关闭缓存读取，强制走网络拉取
    # 强制拉取 3650 天（约 10 年）的历史数据
    TARGET_DAYS = 3650

    symbols = um.get_universe('all_a')
    total = len(symbols)
    logger.info(f"全A股总计 {total} 只股票。 开始检查和补齐数据库...")

    success = 0
    skipped = 0
    failed = 0

    # 随机打乱顺序，分散请求特征
    random.shuffle(symbols)

    # ============== 改用 AKShare 高速通道 ==============
    import akshare as ak
    from datetime import datetime, timedelta

    end_date = datetime.now()
    start_date = end_date - timedelta(days=TARGET_DAYS)
    start_str = start_date.strftime('%Y%m%d')
    end_str = end_date.strftime('%Y%m%d')

    for i, symbol in enumerate(symbols):
        code = symbol.split('.')[0]

        db_df = db.load_klines(code, days=TARGET_DAYS)
        if db_df is not None and len(db_df) > 500:
            logger.debug(f"[{i+1}/{total}] {symbol} 已有 {len(db_df)} 条数据，跳过。")
            skipped += 1
            continue

        logger.info(f"[{i+1}/{total}] 正在通过 AKShare 备用通道同步 {symbol} ({TARGET_DAYS}天) ...")

        market = 'sh' if code.startswith('6') else 'sz'
        ak_symbol = f"{market}{code}"

        try:
            # 轻微休眠，保护接口
            time.sleep(random.uniform(0.1, 0.4))

            # 使用更稳定的新浪接口（复权版）
            df = ak.stock_zh_a_daily(symbol=ak_symbol, start_date=start_str, end_date=end_str, adjust='qfq')

            if not df.empty:
                # 统一列名以适配 DatabaseManager.save_klines
                if 'date' in df.columns: df.rename(columns={'date':'日期'}, inplace=True)
                if 'volume' in df.columns: df.rename(columns={'volume':'成交量'}, inplace=True)

                db.save_klines(code, df)
                logger.success(f"[{i+1}/{total}] {symbol} 同步完成，写入 {len(df)} 条记录。")
                success += 1
            else:
                logger.warning(f"[{i+1}/{total}] {symbol} 拉取为空数据！(可能是退市或停牌)")
                failed += 1

        except Exception as e:
            logger.error(f"[{i+1}/{total}] {symbol} 拉取异常: {e}")
            failed += 1
            time.sleep(2)

    logger.info("============== 全A股数据补齐任务执行完毕 ==============")
    logger.info(f"总计: {total} | 成功新增: {success} | 跳过已下载: {skipped} | 失败: {failed}")

if __name__ == '__main__':
    # 推荐使用 ./manage.sh stop 停掉 web 服务后再执行，以避免并发冲突
    main()
