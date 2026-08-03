"""
基本面数据获取模块

从行情接口中提取股票的基本财务指标：
- PE (市盈率)
- PB (市净率)
- 总市值
"""
import requests
from loguru import logger
import pandas as pd
from data.fetcher import _to_full_code, _session


def get_fundamentals(symbols: list[str]) -> pd.DataFrame:
    """
    批量获取股票基本面数据

    Args:
        symbols: 股票代码列表，如 ['000001', '600000']

    Returns:
        DataFrame, index 为股票代码, 列包含:
        - name: 名称
        - pe: 动态市盈率
        - pb: 市净率
        - market_cap: 总市值(亿)
    """
    if not symbols:
        return pd.DataFrame()

    full_codes = ",".join([_to_full_code(s) for s in symbols])
    url = f"http://qt.gtimg.cn/q={full_codes}"

    try:
        resp = _session.get(url, timeout=10)
        resp.raise_for_status()

        rows = []
        for line in resp.text.strip().split(';'):
            if not line.strip():
                continue

            code_part = line.split('=')[0]
            if '_' not in code_part:
                continue
            symbol = code_part.split('_')[1][2:]  # 提取 000001

            data_str = line.split('"')[1]
            parts = data_str.split('~')

            try:
                rows.append({
                    'symbol': symbol,
                    'name': parts[1],
                    'pe': float(parts[43]) if parts[43] else 0.0,
                    'pb': float(parts[46]) if parts[46] else 0.0,
                    'market_cap': float(parts[45]) if parts[45] else 0.0,
                })
            except (IndexError, ValueError):
                continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df.set_index('symbol', inplace=True)
        return df

    except Exception as e:
        logger.error(f"批量获取基本面数据提取失败: {e}")
        return pd.DataFrame()
