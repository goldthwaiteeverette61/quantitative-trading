# -*- coding: utf-8 -*-
"""
哑铃策略(红利+微盘) — 聚宽回测版

核心逻辑：
1. 新规退市红线排雷：PE>0, PB>0, 非ST, 股价>=4元
2. 哑铃左端(50%仓位)：质量红利压舱石
   - PE<20, PB<2 的低估值安全标的
   - 按 PB 升序 + 波动率升序 综合排名
3. 哑铃右端(50%仓位)：微盘 Q4 温和动量进攻
   - 市值最小的80%标的
   - 取20日动量60%~80%分位（Q4温和区间）
4. 月度轮动
"""

# ===================== 参数配置 ===================== #
from jqdata import *

TOP_N = 20              # 总持仓数量
LEFT_N = 10             # 哑铃左端（红利防守）持仓数
RIGHT_N = 10            # 哑铃右端（微盘进攻）持仓数
REBALANCE_DAY = 1       # 每月第几个交易日调仓
MIN_PRICE = 4.0         # 最低股价

# 交易成本（保守模拟）
STAMP_TAX = 0.0005      # 印花税 0.05%（卖出时收取）
COMMISSION = 0.0003     # 佣金 0.03%
SLIPPAGE = 0.005        # 滑点 0.5%
# ================================================== #


def initialize(context):
    """初始化"""
    set_benchmark('000300.XSHG')

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

    set_option('use_real_price', True)
    set_option('avoid_future_data', True)

    # 过滤停牌、ST、退市股
    set_option('avoid_future_data', True)

    # 每月第 1 个交易日，开盘时间（09:30）执行调仓
    run_monthly(rebalance, monthday=REBALANCE_DAY, time='09:30')

    log.info('=== 哑铃策略(红利+微盘) 初始化完成 ===')
    log.info(f'总持仓: {TOP_N}, 左端红利: {LEFT_N}, 右端微盘: {RIGHT_N}')


def rebalance(context):
    """月度调仓"""
    previous_date = context.previous_date
    log.info(f'>>> [执行时间: {context.current_dt}] 使用截止日期: {previous_date} 开始选股 <<<')

    # ==== 第1步：获取全A股票池 + 退市红线排雷 ==== #
    all_stocks = get_all_securities(types=['stock'], date=previous_date)
    # 过滤ST
    all_stocks = all_stocks[~all_stocks['display_name'].str.contains('ST')]
    stock_list = list(all_stocks.index)

    # 过滤停牌
    paused_info = get_current_data()
    stock_list = [s for s in stock_list if not paused_info[s].paused]

    if not stock_list:
        log.warn('无可用股票')
        return

    # 过滤低价股（股价 >= 4 元）
    price_df = get_price(
        stock_list,
        end_date=previous_date,
        count=1,
        fields=['close'],
        panel=False,
    )
    price_map = price_df.groupby('code')['close'].last().to_dict()
    stock_list = [s for s in stock_list if price_map.get(s, 0) >= MIN_PRICE]

    # 获取基本面数据：PE, PB, 总市值
    q = query(
        valuation.code,
        valuation.pe_ratio,
        valuation.pb_ratio,
        valuation.market_cap,  # 单位：亿元
    ).filter(
        valuation.pe_ratio > 0,   # 亏损股排除
        valuation.pb_ratio > 0,   # 资不抵债排除
    )
    fund_df = get_fundamentals(q, date=previous_date)

    # 本地过滤有效股票池，避免 in_ 语句导致聚宽截断
    if not fund_df.empty:
        fund_df = fund_df[fund_df['code'].isin(stock_list)]

    if fund_df.empty or len(fund_df) < LEFT_N + RIGHT_N:
        log.warn(f'基本面数据不足: {len(fund_df)} 只')
        return

    log.info(f'退市排雷后: {len(fund_df)} 只')

    # ==== 第2步：哑铃左端 — 质量红利压舱石 ==== #
    # 条件：PE < 20, PB < 2
    left_pool = fund_df[
        (fund_df['pe_ratio'] < 20) &
        (fund_df['pb_ratio'] < 2.0)
    ].copy()

    if len(left_pool) < LEFT_N:
        log.warn(f'红利池不足({len(left_pool)}只), 放宽PE限制')
        left_pool = fund_df.copy()

    # 获取20日波动率用于综合排名
    left_candidates = list(left_pool['code'])
    vol_df = get_price(
        left_candidates,
        end_date=previous_date,
        count=20,
        fields=['close'],
        panel=False,
    )

    volatility = {}
    for code, df_code in vol_df.groupby('code'):
        if len(df_code) >= 5:
            returns = df_code['close'].pct_change().dropna()
            volatility[code] = returns.std()
        else:
            volatility[code] = 999  # 数据不足给高波动率

    left_pool['volatility'] = left_pool['code'].map(volatility)

    # 综合打分：PB排名（升序）+ 波动率排名（升序）= 选最便宜最稳的
    left_pool['pb_rank'] = left_pool['pb_ratio'].rank(ascending=True)
    left_pool['vol_rank'] = left_pool['volatility'].rank(ascending=True)
    left_pool['score'] = left_pool['pb_rank'] + left_pool['vol_rank']
    left_pool = left_pool.sort_values('score', ascending=True)

    left_selected = list(left_pool.head(LEFT_N)['code'])
    log.info(f'哑铃左端(红利): {len(left_selected)} 只')
    for code in left_selected:
        row = left_pool[left_pool['code'] == code].iloc[0]
        log.info(f'  {code}: PE={row["pe_ratio"]:.1f}, PB={row["pb_ratio"]:.2f}, 波动率={row["volatility"]:.4f}')

    # ==== 第3步：哑铃右端 — 微盘 Q4 温和动量进攻 ==== #
    # 剔除左端已选股
    right_pool = fund_df[~fund_df['code'].isin(left_selected)].copy()

    # 按市值升序，取最小的80%（小盘弹性标的）
    right_pool = right_pool.sort_values('market_cap', ascending=True)
    right_pool = right_pool.head(int(len(right_pool) * 0.8))

    # 获取20日动量
    right_candidates = list(right_pool['code'])
    mom_df = get_price(
        right_candidates,
        end_date=previous_date,
        count=20,
        fields=['close'],
        panel=False,
    )

    momentum = {}
    for code, df_code in mom_df.groupby('code'):
        if len(df_code) >= 2:
            ret = (df_code['close'].iloc[-1] / df_code['close'].iloc[0] - 1) * 100
            momentum[code] = ret

    right_pool['momentum'] = right_pool['code'].map(momentum)
    right_pool = right_pool.dropna(subset=['momentum'])

    # 按动量排序，取 Q4 区间（60%~80% 分位 = 温和伏击区）
    right_pool = right_pool.sort_values('momentum', ascending=True)
    total_right = len(right_pool)
    start_idx = int(total_right * 0.6)
    end_idx = int(total_right * 0.8)
    q4_pool = right_pool.iloc[start_idx:end_idx]

    if len(q4_pool) >= RIGHT_N:
        right_selected = list(q4_pool.head(RIGHT_N)['code'])
    else:
        # 备用：Q4不够就取动量靠前的
        right_selected = list(right_pool.tail(RIGHT_N)['code'])

    log.info(f'哑铃右端(微盘): {len(right_selected)} 只')
    for code in right_selected:
        mom_val = momentum.get(code, 0)
        log.info(f'  {code}: 20日动量={mom_val:.2f}%')

    # ==== 第4步：合成哑铃 + 执行调仓 ==== #
    all_selected = left_selected + right_selected

    # 卖出不在目标列表中的持仓
    for stock in list(context.portfolio.positions):
        if stock not in all_selected:
            order_target(stock, 0)
            log.info(f'卖出: {stock}')

    total_value = context.portfolio.total_value

    # 左端：防守50%
    if left_selected:
        w_left = 0.50 / len(left_selected)
        for stock in left_selected:
            target_value = total_value * w_left
            order_target_value(stock, target_value)

    # 右端：进攻50%
    if right_selected:
        w_right = 0.50 / len(right_selected)
        for stock in right_selected:
            target_value = total_value * w_right
            order_target_value(stock, target_value)

    log.info(f'>>> 调仓完成: 左端{len(left_selected)}只 + 右端{len(right_selected)}只 = 总{len(all_selected)}只 <<<')
