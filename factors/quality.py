"""
质量、估值与成长因子计算模组 (Quality, Valuation & Growth Factors)

数据来源: 新浪财经三大报表 (via AKShare stock_financial_report_sina)
  - 利润表: 净利润、营业收入、营业成本、研发费用
  - 资产负债表: 资产总计、负债合计、流动资产、流动负债
  - 现金流量表: 经营活动产生的现金流量净额

修订记录:
  - v1: 基础版本 — F-Score / Accruals / R&D Intensity
"""
import pandas as pd
import numpy as np
from typing import Optional
from loguru import logger


def _safe_float(val) -> float:
    """安全地将财报字段转为 float，NaN/None 返回 0"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def fetch_financial_data(symbol: str) -> dict:
    """
    从新浪财经拉取三大报表，返回最近两期年报的关键指标

    Args:
        symbol: 股票代码（纯数字，如 '000001'）

    Returns:
        dict 包含 latest / prev 两期数据，每期包含:
          - report_date: 报告期
          - net_profit: 净利润
          - revenue: 营业收入
          - cost: 营业成本
          - total_assets: 总资产
          - total_liabilities: 总负债
          - current_assets: 流动资产
          - current_liabilities: 流动负债
          - operating_cashflow: 经营活动产生的现金流量净额
          - rd_expense: 研发费用
    """
    import akshare as ak

    code = symbol.split('.')[0]
    result = {'latest': None, 'prev': None}

    try:
        # 拉取三大报表
        income = ak.stock_financial_report_sina(stock=code, symbol='利润表')
        balance = ak.stock_financial_report_sina(stock=code, symbol='资产负债表')
        cashflow = ak.stock_financial_report_sina(stock=code, symbol='现金流量表')
    except Exception as e:
        logger.error(f"[财报获取] {code} 拉取失败: {e}")
        return result

    # 筛选年报（报告日以 1231 结尾），保留最近两期
    def filter_annual(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or '报告日' not in df.columns:
            return pd.DataFrame()
        annual = df[df['报告日'].astype(str).str.endswith('1231')].copy()
        annual = annual.sort_values('报告日', ascending=False).head(2).reset_index(drop=True)
        return annual

    income_annual = filter_annual(income)
    balance_annual = filter_annual(balance)
    cashflow_annual = filter_annual(cashflow)

    def _get_col(df, keywords: list, row_idx: int) -> float:
        """按关键词在列名中模糊匹配取值"""
        if df.empty or row_idx >= len(df):
            return 0.0
        for kw in keywords:
            matches = [c for c in df.columns if kw in c and '小计' not in c and '合计' not in c]
            if matches:
                return _safe_float(df[matches[0]].iloc[row_idx])
        # 如果非 "小计/合计" 没匹配到，放宽条件
        for kw in keywords:
            matches = [c for c in df.columns if kw in c]
            if matches:
                return _safe_float(df[matches[0]].iloc[row_idx])
        return 0.0

    for i, period_key in enumerate(['latest', 'prev']):
        report_date = ''
        if not income_annual.empty and i < len(income_annual):
            report_date = str(income_annual['报告日'].iloc[i])

        data = {
            'report_date': report_date,
            'net_profit': _get_col(income_annual, ['净利润'], i),
            'revenue': _get_col(income_annual, ['营业收入'], i),
            'cost': _get_col(income_annual, ['营业成本', '营业总成本', '营业支出'], i),
            'total_assets': _get_col(balance_annual, ['资产总计'], i),
            'total_liabilities': _get_col(balance_annual, ['负债合计'], i),
            'current_assets': _get_col(balance_annual, ['流动资产'], i),
            'current_liabilities': _get_col(balance_annual, ['流动负债'], i),
            'operating_cashflow': _get_col(cashflow_annual, ['经营活动产生的现金流量净额'], i),
            'rd_expense': _get_col(income_annual, ['研发费用'], i),
        }
        result[period_key] = data

    return result


def compute_fscore(fin_data: dict) -> dict:
    """
    计算 Piotroski F-Score (9分制)

    9项评分标准:
      盈利能力 (4分):
        1. ROA > 0
        2. 经营现金流 > 0
        3. ROA 同比改善 (ΔROA > 0)
        4. 经营现金流 > 净利润（应计质量）
      杠杆/流动性 (3分):
        5. 杠杆率同比下降
        6. 流动比率同比上升
        7. 无新股稀释（简化：净资产占比未下降）
      运营效率 (2分):
        8. 毛利率同比上升
        9. 资产周转率同比上升

    Returns:
        dict with 'fscore' (0-9), 'details' (各项得分), 'roa', 'gross_margin'
    """
    latest = fin_data.get('latest')
    prev = fin_data.get('prev')

    if latest is None:
        return {'fscore': None, 'details': {}, 'roa': None}

    scores = {}

    # 计算当期指标
    ta = latest['total_assets'] or 1e-10
    roa = latest['net_profit'] / ta
    ocf = latest['operating_cashflow']
    leverage = latest['total_liabilities'] / ta
    ca = latest['current_assets'] or 1e-10
    cl = latest['current_liabilities'] or 1e-10
    current_ratio = ca / cl if cl > 0 else 999
    revenue = latest['revenue'] or 1e-10
    gross_margin = (revenue - latest['cost']) / revenue if revenue > 0 else 0
    asset_turnover = revenue / ta

    # 1. ROA > 0
    scores['roa_positive'] = 1 if roa > 0 else 0

    # 2. 经营现金流 > 0
    scores['ocf_positive'] = 1 if ocf > 0 else 0

    # 4. 应计质量: 经营现金流 > 净利润
    scores['accrual_quality'] = 1 if ocf > latest['net_profit'] else 0

    if prev is not None and prev['total_assets'] > 0:
        ta_prev = prev['total_assets']
        roa_prev = prev['net_profit'] / ta_prev
        leverage_prev = prev['total_liabilities'] / ta_prev
        ca_prev = prev['current_assets'] or 1e-10
        cl_prev = prev['current_liabilities'] or 1e-10
        current_ratio_prev = ca_prev / cl_prev if cl_prev > 0 else 999
        revenue_prev = prev['revenue'] or 1e-10
        gross_margin_prev = (revenue_prev - prev['cost']) / revenue_prev if revenue_prev > 0 else 0
        asset_turnover_prev = revenue_prev / ta_prev
        equity_ratio = 1 - leverage
        equity_ratio_prev = 1 - leverage_prev

        # 3. ΔROA > 0
        scores['roa_improving'] = 1 if roa > roa_prev else 0
        # 5. 杠杆率下降
        scores['leverage_declining'] = 1 if leverage < leverage_prev else 0
        # 6. 流动比率上升
        scores['liquidity_improving'] = 1 if current_ratio > current_ratio_prev else 0
        # 7. 净资产占比未下降（无新股稀释代理）
        scores['no_dilution'] = 1 if equity_ratio >= equity_ratio_prev - 0.001 else 0
        # 8. 毛利率上升
        scores['margin_improving'] = 1 if gross_margin > gross_margin_prev else 0
        # 9. 资产周转率上升
        scores['turnover_improving'] = 1 if asset_turnover > asset_turnover_prev else 0
    else:
        # 无同比数据，这6项均给0.5（中性）
        for k in ['roa_improving', 'leverage_declining', 'liquidity_improving',
                   'no_dilution', 'margin_improving', 'turnover_improving']:
            scores[k] = 0

    fscore = sum(scores.values())

    return {
        'fscore': fscore,
        'details': scores,
        'roa': roa,
        'gross_margin': gross_margin,
        'leverage': leverage,
    }


def compute_accruals(fin_data: dict) -> Optional[float]:
    """
    计算应计项目 (Accruals)
    公式: (净利润 - 经营现金流) / 总资产

    物理意义:
      - 值越高：利润中"纸面利润"占比越大，现金支撑越弱，财务造假风险越高
      - 值越低（甚至为负）：利润质量越高，现金流大于账面利润
    """
    latest = fin_data.get('latest')
    if latest is None or latest['total_assets'] <= 0:
        return None

    return (latest['net_profit'] - latest['operating_cashflow']) / latest['total_assets']


def compute_rd_intensity(fin_data: dict) -> Optional[float]:
    """
    计算研发投入强度 (R&D Intensity)
    公式: 研发费用 / 营业收入

    注意: 银行、保险等金融机构通常无研发费用，返回 None
    """
    latest = fin_data.get('latest')
    if latest is None:
        return None

    rd = latest['rd_expense']
    rev = latest['revenue']

    if rd == 0 or rev <= 0:
        return None

    return rd / rev


def compute_quality_factors(symbol: str) -> dict:
    """
    一站式计算某只股票的全部质量因子

    Returns:
        dict with:
          - fscore: Piotroski F-Score (0-9)
          - accruals: 应计项目比率
          - rd_intensity: 研发投入强度 (可能为 None)
          - roa: 资产收益率
          - gross_margin: 毛利率
          - leverage: 杠杆率
          - report_date: 数据所属报告期
    """
    fin_data = fetch_financial_data(symbol)

    if fin_data['latest'] is None:
        logger.warning(f"[质量因子] {symbol} 无财报数据，跳过")
        return {
            'fscore': None, 'accruals': None, 'rd_intensity': None,
            'roa': None, 'gross_margin': None, 'leverage': None,
            'report_date': None,
        }

    fscore_result = compute_fscore(fin_data)
    accruals = compute_accruals(fin_data)
    rd_intensity = compute_rd_intensity(fin_data)

    return {
        'fscore': fscore_result['fscore'],
        'fscore_details': fscore_result['details'],
        'accruals': accruals,
        'rd_intensity': rd_intensity,
        'roa': fscore_result.get('roa'),
        'gross_margin': fscore_result.get('gross_margin'),
        'leverage': fscore_result.get('leverage'),
        'report_date': fin_data['latest']['report_date'],
    }
