# -*- coding: utf-8 -*-
"""
价值动量复合因子策略 — 聚宽回测版

核心逻辑：
1. 估值过滤：PE 低于全市场中位数（排除泡沫股）
2. 动量排序：按 20 日涨幅降序
3. 选前 N 只等权持有
4. 月度轮动

捕捉「便宜又在涨」的机会
"""

# ===================== 参数配置 ===================== #
from jqdata import *

TOP_N = 10            # 持仓数量
PE_QUANTILE = 0.5     # PE 分位数阈值（低于此值才入选）
MIN_PRICE = 3.0       # 最低股价
REBALANCE_DAY = 1     # 每月第几个交易日调仓
CASH_BUFFER = 0.05    # 现金缓冲比例（5%）

# 交易成本（保守模拟）
STAMP_TAX = 0.0005    # 印花税 0.05%（卖出时收取）
COMMISSION = 0.0003   # 佣金 0.03%
SLIPPAGE = 0.005      # 滑点 0.5%
# ================================================== #


def initialize(context):
    """初始化"""
    # 基准：沪深300
    set_benchmark('000300.XSHG')

    # 交易成本设置
    set_order_cost(
        OrderCost(
            open_tax=0,
            close_tax=STAMP_TAX,
            open_commission=COMMISSION,
            close_commission=COMMISSION,
            close_today_commission=0,
            min_commission=5,
        ),
        type='stock'
    )
    set_slippage(FixedSlippage(SLIPPAGE))

    # 使用真实价格（避免未来数据）
    set_option('use_real_price', True)

    # 过滤停牌、ST、退市股
    set_option('avoid_future_data', True)

    # 注册月度定时任务：每月第 1 个交易日，开盘时（09:30）执行调仓
    # 这将完美模拟贴合真实的开盘交易时间
    run_monthly(rebalance, monthday=REBALANCE_DAY, time='09:30')

    log.info('=== 价值动量复合因子策略 初始化完成 ===')
    log.info(f'持仓数: {TOP_N}, PE分位: {PE_QUANTILE}, 最低价: {MIN_PRICE}')


def rebalance(context):
    """月度调仓"""
    previous_date = context.previous_date
    log.info(f'>>> [执行时间: {context.current_dt}] 使用截止日期: {previous_date} 开始选股 <<<')

    # ===== 1. 获取全A股票池 ===== #
    all_stocks = get_all_securities(types=['stock'], date=previous_date)
    # 过滤ST、停牌
    all_stocks = all_stocks[~all_stocks['display_name'].str.contains('ST')]
    stock_list = list(all_stocks.index)

    # 过滤停牌
    paused_info = get_current_data()
    stock_list = [s for s in stock_list if not paused_info[s].paused]

    if not stock_list:
        log.warn('无可用股票')
        return

    # ===== 2. 获取实时价格，过滤低价股 ===== #
    price_df = get_price(
        stock_list,
        end_date=previous_date,
        count=1,
        fields=['close'],
        panel=False,
    )
    if price_df.empty:
        return

    price_map = price_df.groupby('code')['close'].last().to_dict()
    stock_list = [s for s in stock_list if price_map.get(s, 0) >= MIN_PRICE]

    # ===== 3. 获取PE估值数据 ===== #
    # 注意：不要在 in_ 中直接放入几千只股票，可能导致 JQ 数据库截断返回空结果
    q = query(
        valuation.code,
        valuation.pe_ratio,
    ).filter(
        valuation.pe_ratio > 0,  # 排除亏损股
    )
    val_df = get_fundamentals(q, date=previous_date)

    if val_df.empty:
        log.warn('无估值数据')
        return

    # 本地过滤掉已停牌或不足 4 元的股
    val_df = val_df[val_df['code'].isin(stock_list)]

    if val_df.empty:
        log.warn('过滤有效股票后无估值数据')
        return

    # PE 分位筛选
    pe_threshold = val_df['pe_ratio'].quantile(PE_QUANTILE)
    val_df = val_df[val_df['pe_ratio'] <= pe_threshold]
    log.info(f'PE阈值: {pe_threshold:.1f}, 估值筛选后: {len(val_df)} 只')

    if val_df.empty:
        return

    candidates = list(val_df['code'])

    # ===== 4. 获取20日动量 ===== #
    mom_df = get_price(
        candidates,
        end_date=previous_date,
        count=20,
        fields=['close'],
        panel=False,
    )

    if mom_df.empty:
        return

    # 计算20日涨幅
    momentum = {}
    for code, df_code in mom_df.groupby('code'):
        if len(df_code) >= 2:
            ret = (df_code['close'].iloc[-1] / df_code['close'].iloc[0] - 1) * 100
            momentum[code] = ret

    # 按动量降序排列
    sorted_stocks = sorted(momentum.items(), key=lambda x: x[1], reverse=True)
    selected = [s[0] for s in sorted_stocks[:TOP_N]]

    log.info(f'选股 {len(selected)} 只: {selected}')
    for code, mom in sorted_stocks[:TOP_N]:
        log.info(f'  {code}: 20日动量 {mom:.2f}%')

    # ===== 5. 执行调仓 ===== #
    # 卖出不在目标列表中的持仓
    for stock in context.portfolio.positions:
        if stock not in selected:
            order_target(stock, 0)
            log.info(f'卖出: {stock}')

    # 等权买入
    if selected:
        weight = (1 - CASH_BUFFER) / len(selected)
        total_value = context.portfolio.total_value

        for stock in selected:
            target_value = total_value * weight
            order_target_value(stock, target_value)

    log.info(f'>>> 调仓完成, 目标持仓 {len(selected)} 只 <<<')
