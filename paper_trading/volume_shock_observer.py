"""
量能冲击观察策略 — 模拟实盘实时 Worker

目标不是抢交易所级速度，而是观察公开行情里的量能冲击是否存在 1-3 日延续性。
"""

import threading
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from loguru import logger

from paper_trading.breakout_base import get_limit_pct
from paper_trading.shared_market_data import shared_market_data
from data.trading_calendar import (
    MORNING_SCAN_START,
    get_a_share_trading_state,
    is_a_share_trading_day,
    now_shanghai,
)


def _coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def _append_live_metrics(merged: pd.DataFrame) -> pd.DataFrame:
    """
    对 Sina 快照补充 VWAP、实时量比、换手率。

    不同行情源的成交量可能是股或手。这里用 amount / volume 与现价的数量级关系做推断。
    """
    merged = merged.copy()
    _coerce_numeric(
        merged,
        [
            "price",
            "pct_chg",
            "volume",
            "amount",
            "today_open",
            "pre_close_live",
            "pre_close",
            "avg_vol_20d",
            "circ_mv",
        ],
    )

    raw_vwap = np.where(merged["volume"] > 0, merged["amount"] / merged["volume"], 0.0)
    volume_is_lot = raw_vwap > (merged["price"] * 5)
    merged["live_volume_shares"] = np.where(volume_is_lot, merged["volume"] * 100, merged["volume"])
    merged["vwap"] = np.where(volume_is_lot, raw_vwap / 100, raw_vwap)
    merged["vol_ratio_live"] = np.where(
        merged["avg_vol_20d"] > 0,
        merged["live_volume_shares"] / merged["avg_vol_20d"],
        0.0,
    )
    merged["vwap_dev"] = np.where(
        merged["vwap"] > 0,
        (merged["price"] - merged["vwap"]) / merged["vwap"],
        0.0,
    )
    float_shares = np.where(merged["price"] > 0, merged["circ_mv"] / merged["price"], 0.0)
    merged["turnover_pct"] = np.where(
        float_shares > 0,
        merged["live_volume_shares"] / float_shares * 100,
        0.0,
    )
    return merged


def filter_volume_shock_signals(merged: pd.DataFrame, params: dict) -> pd.DataFrame:
    """公开行情量能冲击信号过滤。"""
    if merged.empty:
        return merged.copy()

    data = _append_live_metrics(merged)

    vol_threshold = params.get("vol_threshold", 3.0)
    max_vol_threshold = params.get("max_vol_threshold", 8.0)
    min_pct_chg = params.get("min_pct_chg", 1.0)
    max_pct_chg = params.get("max_pct_chg", 6.0)
    min_amount = params.get("min_amount", 30_000_000.0)
    min_vwap_dev = params.get("min_vwap_dev", 0.0)
    max_vwap_dev = params.get("max_vwap_dev", 0.06)
    min_turnover_pct = params.get("min_turnover_pct", 1.0)
    max_turnover_pct = params.get("max_turnover_pct", 12.0)
    limit_buffer_pct = params.get("limit_buffer_pct", 1.0)

    limit_pct = data["symbol"].astype(str).map(get_limit_pct)
    cond_price = (data["price"] > 0) & (data["today_open"] > 0)
    cond_amount = data["amount"] >= min_amount
    cond_volume = (data["vol_ratio_live"] >= vol_threshold) & (data["vol_ratio_live"] <= max_vol_threshold)
    cond_pct = (data["pct_chg"] >= min_pct_chg) & (data["pct_chg"] <= max_pct_chg)
    cond_above_open = data["price"] > data["today_open"]
    cond_vwap = (data["vwap_dev"] >= min_vwap_dev) & (data["vwap_dev"] <= max_vwap_dev)
    cond_turnover = (data["turnover_pct"] >= min_turnover_pct) & (data["turnover_pct"] <= max_turnover_pct)
    cond_not_limit = data["pct_chg"] <= (limit_pct - limit_buffer_pct)

    signals = data[
        cond_price
        & cond_amount
        & cond_volume
        & cond_pct
        & cond_above_open
        & cond_vwap
        & cond_turnover
        & cond_not_limit
    ].copy()

    if signals.empty:
        return signals

    signals["shock_score"] = (
        signals["vol_ratio_live"] * 2.0
        + signals["turnover_pct"]
        + signals["pct_chg"] * 0.5
        - signals["vwap_dev"].abs() * 10.0
    )
    return signals.sort_values(["shock_score", "amount"], ascending=[False, False])


def volume_shock_buy_remark(row: pd.Series, params: dict) -> str:
    """生成买入备注，便于事后观察。"""
    return (
        f"量能冲击观察: 价{row['price']:.2f} 涨幅{row['pct_chg']:+.1f}% "
        f"量比{row.get('vol_ratio_live', 0):.1f}x "
        f"成交额{row.get('amount', 0) / 1e8:.2f}亿 "
        f"VWAP偏离{row.get('vwap_dev', 0) * 100:+.1f}% "
        f"换手{row.get('turnover_pct', 0):.1f}%"
    )


def _safe_metric_float(value):
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def volume_shock_signal_metrics(row: pd.Series, params: dict) -> dict:
    """生成结构化信号指标，写入 trade_signal_ext。"""
    pre_close_col = "pre_close_live" if "pre_close_live" in row.index else "pre_close"
    metrics = {
        "signal_price": row.get("price"),
        "pct_chg": row.get("pct_chg"),
        "vol_ratio": row.get("vol_ratio_live"),
        "amount": row.get("amount"),
        "vwap": row.get("vwap"),
        "vwap_dev": row.get("vwap_dev"),
        "turnover_pct": row.get("turnover_pct"),
        "shock_score": row.get("shock_score"),
        "today_open": row.get("today_open"),
        "pre_close": row.get(pre_close_col, row.get("pre_close_th", 0)),
    }
    for key in [
        "vol_threshold",
        "max_vol_threshold",
        "min_pct_chg",
        "max_pct_chg",
        "min_amount",
        "min_vwap_dev",
        "max_vwap_dev",
        "min_turnover_pct",
        "max_turnover_pct",
        "limit_buffer_pct",
    ]:
        metrics[f"param.{key}"] = params.get(key)
    return {key: _safe_metric_float(value) for key, value in metrics.items()}


def volume_shock_worker_entry(task_id: str, task_state: dict):
    """量能冲击观察策略实时模拟盘入口。"""
    from data.database import DatabaseManager
    from paper_trading.manager import paper_manager

    account_id = task_state["account_id"]
    stop_event: threading.Event = task_state["_stop_event"]
    max_holdings = int(task_state.get("top_n") or task_state.get("max_holdings") or 5)
    scan_interval = int(task_state.get("scan_interval_sec", 60))
    allocation = task_state.get("allocation", "even_amount")
    fixed_lots = int(task_state.get("fixed_lots", 1))
    trailing_stop_pct = float(task_state.get("trailing_stop_pct", 6.0))
    hard_stop_pct = float(task_state.get("hard_stop_pct", 3.0))
    max_hold_days = int(task_state.get("max_hold_days", 3))

    signal_params = {
        "vol_threshold": float(task_state.get("vol_threshold", 3.0)),
        "max_vol_threshold": float(task_state.get("max_vol_threshold", 8.0)),
        "min_pct_chg": float(task_state.get("min_pct_chg", 1.0)),
        "max_pct_chg": float(task_state.get("max_pct_chg", 6.0)),
        "min_amount": float(task_state.get("min_amount", 30_000_000.0)),
        "min_vwap_dev": float(task_state.get("min_vwap_dev", 0.0)),
        "max_vwap_dev": float(task_state.get("max_vwap_dev", 0.06)),
        "min_turnover_pct": float(task_state.get("min_turnover_pct", 1.0)),
        "max_turnover_pct": float(task_state.get("max_turnover_pct", 12.0)),
        "limit_buffer_pct": float(task_state.get("limit_buffer_pct", 1.0)),
    }

    paper_manager.configure_broker(
        account_id,
        slippage_rate=task_state.get("slippage_rate", 0.005),
        commission_rate=task_state.get("commission_rate", 0.0003),
        stamp_tax_rate=task_state.get("stamp_tax_rate", 0.0005),
    )

    task_state["status"] = "running"
    task_state["message"] = "量能冲击观察启动，等待交易时段"
    _update_task_db_safely(task_id, status="running", message=task_state["message"])

    thresholds: dict = {}
    triggered_today: set[str] = set()
    holdings_tracker: dict[str, dict] = {}
    last_precompute_date = ""
    last_day_end_processed = ""

    def _is_trading_time(now: datetime) -> tuple[bool, str]:
        state = get_a_share_trading_state(now, scan_start=MORNING_SCAN_START, include_precompute=True)
        if state.phase == "precompute":
            return False, "PRECOMPUTE"
        return state.is_trading_time, state.message or ""

    def _precompute_thresholds() -> bool:
        nonlocal thresholds
        task_state["message"] = "盘前预处理：计算量能基准"
        result = shared_market_data.precompute_thresholds(
            vol_threshold=signal_params["vol_threshold"],
            min_list_days=int(task_state.get("min_list_days", 60)),
            min_price=float(task_state.get("min_price", 3.0)),
            min_circ_mv=float(task_state.get("min_circ_mv", 20e8)),
            max_circ_mv=float(task_state.get("max_circ_mv", 500e8)),
            caller=f"VOLUME-SHOCK-{task_id}",
        )
        thresholds = result or {}
        if thresholds:
            task_state["message"] = f"量能基准完成：{len(thresholds)} 只股票"
            return True
        task_state["message"] = "量能基准为空，等待重试"
        return False

    def _restore_holdings():
        nonlocal holdings_tracker
        try:
            db = DatabaseManager()
            positions = db.get_paper_positions(account_id)
            trades = db.get_paper_trade_log(account_id)
            last_buy_map = {}
            for trade in trades:
                sym = trade.get("symbol", "")
                if trade.get("action") == "BUY" and sym not in last_buy_map:
                    last_buy_map[sym] = trade

            today = now_shanghai()
            for pos in positions:
                sym = pos["symbol"]
                if pos["shares"] <= 0:
                    continue
                buy_trade = last_buy_map.get(sym, {})
                buy_time = buy_trade.get("trade_time", "")
                buy_price = float(buy_trade.get("price", pos["cost_price"]) or pos["cost_price"])
                buy_date = buy_time[:10] if buy_time else today.strftime("%Y-%m-%d")
                try:
                    buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
                    delta_days = max((today - buy_dt).days, 0)
                    days_held = max(sum(1 for d in range(delta_days) if (buy_dt + timedelta(days=d)).weekday() < 5) - 1, 0)
                except Exception:
                    days_held = 0

                holdings_tracker[sym] = {
                    "buy_date": buy_date,
                    "buy_price": buy_price,
                    "name": sym,
                    "days_held": days_held,
                    "highest_price": buy_price,
                    "pre_close": 0.0,
                }

            if holdings_tracker:
                task_state["message"] = f"已恢复 {len(holdings_tracker)} 只持仓"
        except Exception as exc:
            logger.error(f"[量能冲击 {task_id}] 恢复持仓失败: {exc}")

    def _scan_market(snap: pd.DataFrame):
        nonlocal triggered_today
        th_df = pd.DataFrame.from_dict(thresholds, orient="index")
        th_df.index.name = "symbol"
        merged = snap.merge(th_df, left_on="symbol", right_index=True, how="inner", suffixes=("_live", "_th"))
        if merged.empty:
            task_state["message"] = f"扫描完成：无匹配标的，持仓 {len(holdings_tracker)}/{max_holdings}"
            return

        merged = merged[~merged["symbol"].isin(triggered_today)]
        merged = merged[~merged["symbol"].isin(set(holdings_tracker.keys()))]
        signals = filter_volume_shock_signals(merged, signal_params)

        available_slots = max_holdings - len(holdings_tracker)
        now_str = now_shanghai().strftime("%H:%M:%S")
        if signals.empty or available_slots <= 0:
            reason = "仓位已满" if available_slots <= 0 else "无信号"
            task_state["message"] = (
                f"{now_str} 扫描完成：{reason} | 持仓 {len(holdings_tracker)}/{max_holdings} "
                f"| 今日触发 {len(triggered_today)}"
            )
            return

        buy_candidates = signals.head(available_slots)
        for _, row in buy_candidates.iterrows():
            sym = row["symbol"]
            name = row.get("name", sym)
            price = float(row["price"])
            account = paper_manager.get_account_detail(account_id)
            cash = account.get("cash", 0.0)
            remark = volume_shock_buy_remark(row, signal_params)

            if allocation == "fixed_lots":
                shares = fixed_lots * 100
                if shares * price * 1.003 > cash:
                    triggered_today.add(sym)
                    logger.warning(f"[量能冲击 {task_id}] 现金不足，跳过 {sym}")
                    continue
                ok = paper_manager.execute_buy(account_id, sym, shares=shares, remark=remark)
            else:
                total_asset = account.get("total_value", cash)
                per_stock = min(total_asset * 0.95 / max_holdings, cash * 0.95)
                ok = paper_manager.execute_buy(account_id, sym, amount=per_stock, remark=remark)

            triggered_today.add(sym)
            if not ok:
                logger.warning(f"[量能冲击 {task_id}] 买入失败: {sym}({name})")
                continue

            trade_id = paper_manager.get_last_trade_id(account_id)
            if trade_id:
                try:
                    DatabaseManager().log_trade_signal_metrics(
                        trade_id=trade_id,
                        task_id=task_id,
                        account_id=account_id,
                        strategy_key="volume_shock_observer",
                        symbol=sym,
                        signal_time=now_shanghai().strftime("%Y-%m-%d %H:%M:%S"),
                        metrics=volume_shock_signal_metrics(row, signal_params),
                    )
                except Exception as exc:
                    logger.warning(f"[量能冲击 {task_id}] 信号指标扩展写入失败 {sym}: {exc}")

            pre_close_col = "pre_close_live" if "pre_close_live" in row.index else "pre_close"
            pre_close = float(row.get(pre_close_col, 0) or row.get("pre_close_th", 0) or 0)
            holdings_tracker[sym] = {
                "buy_date": now_shanghai().strftime("%Y-%m-%d"),
                "buy_price": price,
                "name": name,
                "days_held": 0,
                "highest_price": price,
                "pre_close": pre_close,
            }
            task_state["rebalance_count"] = task_state.get("rebalance_count", 0) + 1
            task_state["message"] = (
                f"{now_str} 买入 {sym}({name}) 价{price:.2f} "
                f"量比{row.get('vol_ratio_live', 0):.1f}x 涨幅{row.get('pct_chg', 0):+.1f}% "
                f"| 持仓 {len(holdings_tracker)}/{max_holdings}"
            )
            _update_task_db_safely(task_id, message=task_state["message"], rebalance_count=task_state["rebalance_count"])

    def _check_positions(snap: pd.DataFrame):
        if not holdings_tracker:
            return
        snap = _coerce_numeric(snap.copy(), ["price", "pct_chg"])
        live_map = snap.set_index("symbol")[["price", "pct_chg"]].to_dict("index")
        today_str = now_shanghai().strftime("%Y-%m-%d")
        to_sell: list[tuple[str, str]] = []

        for sym, info in list(holdings_tracker.items()):
            live = live_map.get(sym)
            if not live:
                continue

            price = float(live["price"])
            pct_chg = float(live["pct_chg"])
            buy_price = float(info.get("buy_price", 0))
            highest_price = float(info.get("highest_price", buy_price))
            days_held = int(info.get("days_held", 0))
            pre_close = float(info.get("pre_close", 0))

            if price > highest_price:
                info["highest_price"] = price
                highest_price = price

            if info.get("buy_date") == today_str:
                continue

            if pct_chg >= get_limit_pct(sym) - 0.5:
                continue

            if buy_price <= 0:
                continue

            pnl_pct = (price - buy_price) / buy_price * 100
            drawdown_pct = (highest_price - price) / highest_price * 100 if highest_price > 0 else 0

            if pre_close > 0 and price < pre_close:
                to_sell.append((sym, f"量能失败：跌破触发日前收{pre_close:.2f}，收益{pnl_pct:+.1f}%"))
            elif pnl_pct <= -hard_stop_pct:
                to_sell.append((sym, f"硬止损：收益{pnl_pct:+.1f}%"))
            elif drawdown_pct >= trailing_stop_pct:
                to_sell.append((sym, f"移动止盈：高点回撤{drawdown_pct:.1f}%，收益{pnl_pct:+.1f}%"))
            elif days_held >= 1 and pnl_pct < 0.5:
                to_sell.append((sym, f"次日不强：持有{days_held}天，收益{pnl_pct:+.1f}%"))
            elif days_held >= max_hold_days:
                to_sell.append((sym, f"到期退出：持有{days_held}天，收益{pnl_pct:+.1f}%"))

        for sym, reason in to_sell:
            ok = paper_manager.execute_sell(account_id, sym, 0, remark=reason)
            if ok:
                logger.info(f"[量能冲击 {task_id}] 卖出 {sym}: {reason}")
                holdings_tracker.pop(sym, None)
            else:
                logger.warning(f"[量能冲击 {task_id}] 卖出失败 {sym}: {reason}")

        if to_sell:
            task_state["message"] = f"卖出 {len(to_sell)} 只 | 持仓 {len(holdings_tracker)}/{max_holdings}"
            _update_task_db_safely(task_id, message=task_state["message"])

    def _increment_hold_days():
        for sym in holdings_tracker:
            holdings_tracker[sym]["days_held"] += 1

    _restore_holdings()
    logger.info(f"[量能冲击 {task_id}] Worker 启动，账户 {account_id}")

    while not stop_event.is_set():
        now = now_shanghai()
        today_str = now.strftime("%Y-%m-%d")
        is_trading, status = _is_trading_time(now)

        if status == "PRECOMPUTE" and last_precompute_date != today_str:
            _precompute_thresholds()
            last_precompute_date = today_str
            triggered_today.clear()
            for _ in range(300):
                if stop_event.is_set():
                    break
                time.sleep(1)
            continue

        if not is_trading:
            if is_a_share_trading_day(now.date()) and now.hour >= 15 and now.minute >= 5 and last_day_end_processed != today_str:
                _increment_hold_days()
                last_day_end_processed = today_str
            task_state["message"] = status
            _update_task_db_safely(task_id, message=task_state["message"])
            for _ in range(30):
                if stop_event.is_set():
                    break
                time.sleep(1)
            continue

        if not thresholds:
            _precompute_thresholds()
            if not thresholds:
                for _ in range(60):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
                continue

        try:
            snapshot = shared_market_data.fetch_snapshot(caller=f"VOLUME-SHOCK-{task_id}")
            if snapshot is not None:
                _scan_market(snapshot)
                _check_positions(snapshot)
            else:
                task_state["message"] = "快照为空，等待重试"
        except Exception as exc:
            task_state["message"] = f"扫描异常: {str(exc)[:80]}"
            logger.error(f"[量能冲击 {task_id}] 扫描异常: {exc}")

        for _ in range(scan_interval):
            if stop_event.is_set():
                break
            time.sleep(1)

    task_state["status"] = "stopped"
    task_state["message"] = "量能冲击观察已停止"
    _update_task_db_safely(task_id, status="stopped", message=task_state["message"])
    logger.info(f"[量能冲击 {task_id}] Worker 已停止")


def _update_task_db_safely(task_id: str, **kwargs):
    try:
        import sys

        update_fn = getattr(sys.modules.get("__main__"), "_update_task_db", None)
        if update_fn is None:
            update_fn = getattr(sys.modules.get("server"), "_update_task_db", None)
        if update_fn is None:
            from server import _update_task_db as update_fn

        update_fn(task_id, **kwargs)
    except Exception:
        pass
