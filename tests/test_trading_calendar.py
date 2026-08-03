from datetime import datetime

import pandas as pd

from data.trading_calendar import (
    filter_a_share_trading_dates,
    get_a_share_order_state,
    get_a_share_trading_state,
    is_a_share_trading_day,
)


def test_a_share_trading_day_blocks_weekend_and_known_holiday():
    assert is_a_share_trading_day("2026-08-03")
    assert not is_a_share_trading_day("2026-08-01")
    assert not is_a_share_trading_day("2026-10-01")


def test_order_gate_blocks_lunch_tail_and_after_close():
    assert get_a_share_order_state(datetime(2026, 8, 3, 10, 0)).is_trading_time
    assert not get_a_share_order_state(datetime(2026, 8, 3, 11, 45)).is_trading_time
    assert get_a_share_order_state(datetime(2026, 8, 3, 14, 54, 59)).is_trading_time
    assert not get_a_share_order_state(datetime(2026, 8, 3, 14, 55)).is_trading_time
    assert not get_a_share_order_state(datetime(2026, 8, 3, 15, 0, 1)).is_trading_time


def test_precompute_and_scan_start_share_the_same_calendar():
    precompute = get_a_share_trading_state(
        datetime(2026, 8, 3, 9, 20),
        include_precompute=True,
    )
    assert precompute.phase == "precompute"
    assert precompute.message == "PRECOMPUTE"

    quiet = get_a_share_trading_state(
        datetime(2026, 8, 3, 9, 32),
        scan_start=datetime(2026, 8, 3, 9, 35).time(),
    )
    assert quiet.phase == "morning_quiet"
    assert not quiet.is_trading_time


def test_backtest_date_filter_removes_non_trading_dates():
    values = [
        pd.Timestamp("2026-07-31"),
        pd.Timestamp("2026-08-01"),
        pd.Timestamp("2026-10-01"),
    ]
    assert filter_a_share_trading_dates(values) == [pd.Timestamp("2026-07-31")]
