"""
动能突破策略 — 统一 Worker 基座

将 V1/V2/V3 共享的全部逻辑（~85%）抽取至此：
    - 持仓恢复（_restore_holdings）
    - 交易时段判断（_is_trading_time）
    - 盘前预处理委托（_precompute_thresholds）
    - 持仓管理 / 卖出逻辑（_check_positions）
    - 收盘持仓天数递增（_increment_hold_days）
    - 主循环状态机 + 容错

各版本仅需提供：
    - signal_filter_fn(merged, pre_close_col, params) -> DataFrame  信号过滤
    - buy_remark_fn(row, params) -> str  买入备注

通过 breakout_worker_entry(version, ...) 统一调度。
"""

import time
import threading
from datetime import datetime, timedelta
from loguru import logger
import pandas as pd
import numpy as np

from paper_trading.shared_market_data import shared_market_data
from data.trading_calendar import (
    MORNING_SCAN_START,
    get_a_share_trading_state,
    is_a_share_trading_day,
    now_shanghai,
)


# ===========================================================================
#  公共工具
# ===========================================================================

def get_limit_pct(symbol: str) -> float:
    """根据股票代码判定涨停板幅度。

    - 主板（60xxxx / 00xxxx）：10%
    - 创业板（300xxx）：20%
    - 科创板（688xxx）：20%
    - 北交所（8xxxxx / 4xxxxx）：30%
    """
    if symbol.startswith('300') or symbol.startswith('688'):
        return 20.0
    elif symbol.startswith('8') or symbol.startswith('4'):
        return 30.0
    return 10.0


# ===========================================================================
#  版本信号分发注册表
# ===========================================================================

_SIGNAL_REGISTRY: dict = {}


def _update_task_db_safely(task_id: str, **kwargs):
    try:
        import sys

        update_fn = getattr(sys.modules.get('__main__'), '_update_task_db', None)
        if update_fn is None:
            update_fn = getattr(sys.modules.get('server'), '_update_task_db', None)
        if update_fn is None:
            from server import _update_task_db as update_fn

        update_fn(task_id, **kwargs)
    except Exception:
        pass


def _get_signal_funcs(version: str):
    """惰性加载信号过滤函数，避免启动时 import 所有版本"""
    if version not in _SIGNAL_REGISTRY:
        if version == 'v1':
            from paper_trading.breakout_v1_signals import v1_signal_filter, v1_buy_remark
            _SIGNAL_REGISTRY['v1'] = (v1_signal_filter, v1_buy_remark)
        elif version == 'v1_1':
            from paper_trading.breakout_v1_1_signals import v1_1_signal_filter, v1_1_buy_remark
            _SIGNAL_REGISTRY['v1_1'] = (v1_1_signal_filter, v1_1_buy_remark)
        elif version == 'v2':
            from paper_trading.breakout_v2_signals import v2_signal_filter, v2_buy_remark
            _SIGNAL_REGISTRY['v2'] = (v2_signal_filter, v2_buy_remark)
        elif version == 'v2_1':
            from paper_trading.breakout_v2_1_signals import v2_1_signal_filter, v2_1_buy_remark
            _SIGNAL_REGISTRY['v2_1'] = (v2_1_signal_filter, v2_1_buy_remark)
        elif version == 'v3':
            from paper_trading.breakout_v3_signals import v3_signal_filter, v3_buy_remark
            _SIGNAL_REGISTRY['v3'] = (v3_signal_filter, v3_buy_remark)
        elif version == 'v3_1':
            from paper_trading.breakout_v3_1_signals import v3_1_signal_filter, v3_1_buy_remark
            _SIGNAL_REGISTRY['v3_1'] = (v3_1_signal_filter, v3_1_buy_remark)
        else:
            raise ValueError(f'未知的动能突破版本: {version}')
    return _SIGNAL_REGISTRY[version]


# ===========================================================================
#  统一 Worker 入口
# ===========================================================================

def breakout_worker_entry(version: str, task_id: str, task_state: dict, strategies: dict):
    """
    动能突破策略的统一实时盘中扫描 Worker。

    由 server.py 在独立线程中启动。
    version: 'v1' | 'v2' | 'v3'
    task_state: STRATEGY_TASKS[task_id] 的引用，线程安全地更新前端显示。
    """
    from paper_trading.manager import paper_manager
    from data.database import DatabaseManager

    signal_filter_fn, buy_remark_fn = _get_signal_funcs(version)
    version_label = version.upper()

    task_state['status'] = 'running'
    task_state['message'] = f'🚀 动能突破{version_label} 实时监控启动中...'
    stop_event: threading.Event = task_state['_stop_event']

    # ===== 策略参数 =====
    vol_threshold = task_state.get('vol_threshold', 3.0)
    max_vol_threshold = task_state.get('max_vol_threshold', 5.0)
    yest_vol_max_ratio = task_state.get('yest_vol_max_ratio', 2.0)
    min_pct_chg = task_state.get('min_pct_chg', 5.0)
    max_holdings = task_state.get('max_holdings', 5)
    trailing_stop_pct = task_state.get('trailing_stop_pct', 8.0)
    scan_interval = task_state.get('scan_interval_sec', 60)
    min_list_days = 60
    min_price = task_state.get('min_price', 3.0)
    min_circ_mv = task_state.get('min_circ_mv', 20e8)
    max_circ_mv = task_state.get('max_circ_mv', 500e8)
    allocation = task_state.get('allocation', 'even_amount')
    fixed_lots = int(task_state.get('fixed_lots', 1))

    # 构建传递给信号过滤函数的参数包
    signal_params = {
        'task_id': task_id,
        'vol_threshold': vol_threshold,
        'max_vol_threshold': max_vol_threshold,
        'yest_vol_max_ratio': yest_vol_max_ratio,
        'min_pct_chg': min_pct_chg,
        # V1-1 / V2-1 / V3-1 共享（防飞刀）
        'max_vwap_dev': task_state.get('max_vwap_dev', 0.10),
        'max_turnover_pct': task_state.get('max_turnover_pct', 15.0),
        # V2 / V2-1 特有
        'min_vwap_dev': task_state.get('min_vwap_dev', 0.02),
        'min_turnover_pct': task_state.get('min_turnover_pct', 5.0),
        # V3 / V3-1 特有
        'atr_multiplier': task_state.get('atr_multiplier', 1.5),
        'min_pct_floor': task_state.get('min_pct_floor', 1.0),
    }

    account_id = task_state['account_id']

    # ===== 运行时状态 =====
    thresholds: dict = {}
    triggered_today: set = set()
    holdings_tracker: dict = {}
    last_precompute_date: str = ''

    # ------------------------------------------------------------------
    #  持仓恢复
    # ------------------------------------------------------------------
    def _restore_holdings():
        nonlocal holdings_tracker
        try:
            db = DatabaseManager()
            positions = db.get_paper_positions(account_id)
            if not positions:
                logger.info(f'[动能突破 {task_id}] 无持仓需恢复')
                return

            trades = db.get_paper_trade_log(account_id)
            last_buy_map = {}
            for t in trades:
                sym = t.get('symbol', '')
                if t.get('action') == 'BUY' and sym not in last_buy_map:
                    last_buy_map[sym] = t

            today = now_shanghai()
            restored = 0
            for pos in positions:
                sym = pos['symbol']
                if pos['shares'] <= 0:
                    continue

                buy_trade = last_buy_map.get(sym, {})
                buy_time_str = buy_trade.get('trade_time', '')
                buy_price = buy_trade.get('price', pos['cost_price'])

                if buy_time_str:
                    try:
                        buy_dt = datetime.strptime(buy_time_str[:10], '%Y-%m-%d')
                        delta_days = (today - buy_dt).days
                        trading_days = sum(
                            1 for d in range(delta_days)
                            if (buy_dt + timedelta(days=d)).weekday() < 5
                        ) - 1
                        trading_days = max(trading_days, 0)
                    except Exception:
                        trading_days = 0
                    buy_date = buy_time_str[:10]
                else:
                    trading_days = 0
                    buy_date = today.strftime('%Y-%m-%d')

                pre_close = 0.0
                if sym in thresholds:
                    pre_close = thresholds[sym].get('pre_close', 0)

                holdings_tracker[sym] = {
                    'buy_date': buy_date,
                    'buy_price': float(buy_price),
                    'name': sym,
                    'days_held': trading_days,
                    'highest_price': float(buy_price),
                    'pre_close': pre_close,
                }
                restored += 1

            if restored > 0:
                logger.info(
                    f'[动能突破 {task_id}] 从数据库恢复 {restored} 只持仓: '
                    f'{", ".join(holdings_tracker.keys())}'
                )
                task_state['message'] = f'♻️ 已恢复 {restored} 只持仓，持仓 {restored}/{max_holdings}'
        except Exception as e:
            logger.error(f'[动能突破 {task_id}] 恢复持仓失败: {e}')

    # ------------------------------------------------------------------
    #  交易时段判断
    # ------------------------------------------------------------------
    def _is_trading_time(now: datetime) -> tuple[bool, str]:
        state = get_a_share_trading_state(now, scan_start=MORNING_SCAN_START, include_precompute=True)
        if state.phase == 'precompute':
            return False, 'PRECOMPUTE'
        return state.is_trading_time, f'⏸️ {state.message}' if state.message else ''

    # ------------------------------------------------------------------
    #  盘前预处理
    # ------------------------------------------------------------------
    def _precompute_thresholds():
        nonlocal thresholds
        task_state['message'] = '📊 盘前预处理：正在计算阈值...'
        result = shared_market_data.precompute_thresholds(
            vol_threshold=vol_threshold,
            min_list_days=min_list_days,
            min_price=min_price,
            min_circ_mv=min_circ_mv,
            max_circ_mv=max_circ_mv,
            caller=f'{version_label}-{task_id}',
        )
        thresholds = result
        if thresholds:
            task_state['message'] = f'✅ 盘前预处理完成：{len(thresholds)} 只股票已建立阈值'
            return True
        else:
            task_state['message'] = '⚠️ 盘前预处理失败'
            return False

    # ------------------------------------------------------------------
    #  盘中扫描（信号过滤委托给版本特定函数）
    # ------------------------------------------------------------------
    def _scan_market(snap: pd.DataFrame):
        nonlocal triggered_today

        try:
            now = now_shanghai()
            if now.hour == 9 and now.minute < 35:
                task_state['message'] = f'⏸️ 早盘静默期（{now.strftime("%H:%M")}），等待 9:35 启动扫描...'
                return

            task_state['message'] = f'🔍 正在扫描全市场... 持仓 {len(holdings_tracker)}/{max_holdings}'

            th_df = pd.DataFrame.from_dict(thresholds, orient='index')
            th_df.index.name = 'symbol'

            merged = snap.merge(th_df, left_on='symbol', right_index=True, how='inner',
                                suffixes=('_live', '_th'))

            if merged.empty:
                task_state['message'] = f'🔍 扫描完成（无匹配标的），持仓 {len(holdings_tracker)}/{max_holdings}'
                return

            merged = merged[~merged['symbol'].isin(triggered_today)]
            merged = merged[~merged['symbol'].isin(set(holdings_tracker.keys()))]

            if merged.empty:
                return

            pre_close_col = 'pre_close_th' if 'pre_close_th' in merged.columns else 'pre_close'

            # ===== 委托版本特定的信号过滤 =====
            signals = signal_filter_fn(merged, pre_close_col, signal_params)

            # 按量比降序排列
            if not signals.empty:
                signals['vol_ratio_live'] = signals['volume'] / signals['avg_vol_20d']
                signals = signals.sort_values('vol_ratio_live', ascending=False)

            available_slots = max_holdings - len(holdings_tracker)

            if signals.empty or available_slots <= 0:
                status_msg = '仓位已满' if available_slots <= 0 else '无信号'
                now_str = now_shanghai().strftime('%H:%M:%S')
                task_state['message'] = (
                    f'🔍 {now_str} 扫描完成（{status_msg}）'
                    f' | 持仓 {len(holdings_tracker)}/{max_holdings}'
                    f' | 今日已触发 {len(triggered_today)} 只'
                )
                return

            buy_candidates = signals.head(available_slots)
            now_str = now_shanghai().strftime('%H:%M:%S')

            for _, row in buy_candidates.iterrows():
                sym = row['symbol']
                name = row.get('name', sym)
                price = row['price']
                pct = row['pct_chg']
                vr = row.get('vol_ratio_live', 0)

                logger.info(
                    f'[动能突破 {task_id}] 🔥 信号触发: '
                    f'{sym}({name}) ¥{price:.2f} +{pct:.1f}% 量比:{vr:.1f}x'
                )

                acc_info = paper_manager.get_account_detail(account_id)
                cash = acc_info.get('cash', 0)

                if allocation == 'fixed_lots':
                    buy_shares = fixed_lots * 100
                    cost_estimate = buy_shares * price * 1.003
                    if cost_estimate > cash:
                        logger.warning(
                            f'[动能突破 {task_id}] 现金不足: '
                            f'{sym} 需 ¥{cost_estimate:.0f}, 可用 ¥{cash:.0f}, 跳过'
                        )
                        continue

                    remark = buy_remark_fn(row, signal_params)
                    ok = paper_manager.execute_buy(account_id, sym, shares=buy_shares, remark=remark)
                else:
                    total_asset = acc_info.get('total_value', acc_info.get('cash', 0))
                    per_stock = total_asset * 0.95 / max_holdings
                    per_stock = min(per_stock, cash * 0.95)
                    remark = buy_remark_fn(row, signal_params)
                    ok = paper_manager.execute_buy(account_id, sym, amount=per_stock, remark=remark)

                if ok:
                    pre_close_col_live = 'pre_close_live' if 'pre_close_live' in row.index else 'pre_close'
                    live_pre_close = row.get(pre_close_col_live, 0)
                    if live_pre_close == 0 or pd.isna(live_pre_close):
                        live_pre_close = row.get(pre_close_col, 0)

                    holdings_tracker[sym] = {
                        'buy_date': now_shanghai().strftime('%Y-%m-%d'),
                        'buy_price': price,
                        'name': name,
                        'days_held': 0,
                        'highest_price': price,
                        'pre_close': float(live_pre_close) if live_pre_close else 0,
                    }
                    triggered_today.add(sym)
                    task_state['message'] = (
                        f'🔥 {now_str} 买入 {sym}({name}) '
                        f'¥{price:.2f} +{pct:.1f}% 量比:{vr:.1f}x'
                        f' | 持仓 {len(holdings_tracker)}/{max_holdings}'
                    )
                else:
                    triggered_today.add(sym)
                    logger.warning(f'[动能突破 {task_id}] 买入失败: {sym}({name})')

        except Exception as e:
            err_msg = str(e)[:80]
            if 'Proxy' in err_msg or 'Connection' in err_msg or 'timeout' in err_msg:
                task_state['message'] = f'⚠️ 网络异常，60秒后重试... ({err_msg[:40]})'
            else:
                task_state['message'] = f'⚠️ 扫描异常: {err_msg}'
            logger.error(f'[动能突破 {task_id}] 扫描异常: {e}')

    # ------------------------------------------------------------------
    #  持仓检查（卖出逻辑）
    # ------------------------------------------------------------------
    def _check_positions(snap: pd.DataFrame):
        if not holdings_tracker:
            return

        try:
            snap_indexed = snap.set_index('symbol')[['price', 'pct_chg']]
            snap_dict = snap_indexed.to_dict('index')

            to_sell = []
            today_str = now_shanghai().strftime('%Y-%m-%d')

            for sym, info in list(holdings_tracker.items()):
                live = snap_dict.get(sym)
                if not live:
                    continue

                current_price = live['price']
                buy_price = info.get('buy_price', 0)
                pre_close = info.get('pre_close', 0)
                highest_price = info.get('highest_price', current_price)
                days_held = info.get('days_held', 0)
                pct_chg = live['pct_chg']

                if current_price > highest_price:
                    info['highest_price'] = current_price
                    highest_price = current_price

                # T+1 守卫
                if info.get('buy_date') == today_str:
                    continue

                # 涨停豁免
                limit_pct = get_limit_pct(sym)
                if pct_chg >= limit_pct - 0.5:
                    continue

                if buy_price <= 0:
                    continue

                pnl_pct = (current_price - buy_price) / buy_price * 100

                # 离场1：破位止损
                if pre_close > 0 and current_price < pre_close:
                    to_sell.append((sym, f'破位止损(穿透昨收¥{pre_close:.2f}) 收益 {pnl_pct:+.1f}%'))
                    continue

                # 离场2：移动止盈
                if highest_price > 0:
                    drawdown = (highest_price - current_price) / highest_price * 100
                    if drawdown >= trailing_stop_pct:
                        to_sell.append((sym, f'动态止盈(高点¥{highest_price:.2f}回撤{drawdown:.1f}%) 锁定收益 {pnl_pct:+.1f}%'))
                        continue

                # 离场3：动能衰竭
                if days_held >= 2 and pnl_pct < 2.0:
                    to_sell.append((sym, f'动能衰竭(持有{days_held}天不涨) 离场收益 {pnl_pct:+.1f}%'))
                    continue

            for sym, reason in to_sell:
                ok = paper_manager.execute_sell(account_id, sym, 0, remark=reason)
                name = holdings_tracker.get(sym, {}).get('name', sym)
                if ok:
                    logger.info(f'[动能突破 {task_id}] 卖出 {sym}({name}): {reason}')
                    del holdings_tracker[sym]
                else:
                    logger.error(f'[动能突破 {task_id}] 卖出失败 {sym}({name}): {reason}')

            if to_sell:
                task_state['message'] = (
                    f'📤 卖出 {len(to_sell)} 只 '
                    f'| 持仓 {len(holdings_tracker)}/{max_holdings}'
                )

        except Exception as e:
            logger.error(f'[动能突破 {task_id}] 持仓检查异常: {e}')

    # ------------------------------------------------------------------
    #  收盘天数递增
    # ------------------------------------------------------------------
    def _increment_hold_days():
        for sym in holdings_tracker:
            holdings_tracker[sym]['days_held'] += 1

    # ==================================================================
    #  启动 + 主循环
    # ==================================================================
    _restore_holdings()

    logger.info(f'[动能突破 {task_id}] Worker 启动（{version_label}），账户: {account_id}，持仓 {len(holdings_tracker)} 只')

    _update_task_db_safely(task_id, status='running', message=task_state['message'])

    last_day_end_processed = ''

    while not stop_event.is_set():
        now = now_shanghai()
        today_str = now.strftime('%Y-%m-%d')

        is_trading, status_msg = _is_trading_time(now)

        # 盘前预处理
        if status_msg == 'PRECOMPUTE' and last_precompute_date != today_str:
            _precompute_thresholds()
            last_precompute_date = today_str
            triggered_today.clear()
            for _ in range(300):
                if stop_event.is_set():
                    break
                time.sleep(1)
            continue

        if not is_trading:
            if (is_a_share_trading_day(now.date()) and now.hour >= 15 and now.minute >= 5
                    and last_day_end_processed != today_str):
                _increment_hold_days()
                last_day_end_processed = today_str
                logger.info(
                    f'[动能突破 {task_id}] 收盘处理完成，'
                    f'持仓 {len(holdings_tracker)} 只，'
                    f'今日触发 {len(triggered_today)} 只'
                )

            task_state['message'] = status_msg
            for _ in range(30):
                if stop_event.is_set():
                    break
                time.sleep(1)
            continue

        # ===== 盘中扫描 =====
        if not thresholds:
            _precompute_thresholds()
            if not thresholds:
                task_state['message'] = '⚠️ 阈值数据为空，等待重试...'
                for _ in range(60):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
                continue

        # 拉取共享快照（双重容错：shared_market_data 内部已有 try/except，这里再兜底）
        try:
            snapshot = shared_market_data.fetch_snapshot(caller=f'{version_label}-{task_id}')
        except Exception as e:
            logger.warning(f'[动能突破 {task_id}] 快照拉取异常，本轮跳过: {e}')
            snapshot = None

        if snapshot is not None:
            _scan_market(snapshot)
            _check_positions(snapshot)
        else:
            task_state['message'] = '⚠️ 快照数据为空，等待重试...'

        for _ in range(scan_interval):
            if stop_event.is_set():
                break
            time.sleep(1)

    # ===== 退出清理 =====
    task_state['status'] = 'stopped'
    task_state['message'] = '🛑 动能突破实时监控已停止'
    _update_task_db_safely(task_id, status='stopped', message=task_state['message'])
    logger.info(f'[动能{version_label} {task_id}] Worker 已停止')
