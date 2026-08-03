import pandas as pd

from paper_trading.volume_shock_observer import (
    filter_volume_shock_signals,
    volume_shock_buy_remark,
    volume_shock_signal_metrics,
)


def test_filter_volume_shock_signals_accepts_controlled_shock():
    df = pd.DataFrame([
        {
            "symbol": "000001",
            "name": "有效样本",
            "price": 10.20,
            "pct_chg": 2.0,
            "volume": 40_000,       # 手
            "amount": 40_800_000,   # 元
            "today_open": 10.00,
            "avg_vol_20d": 1_000_000,
            "circ_mv": 1_020_000_000,
        },
        {
            "symbol": "000002",
            "name": "涨幅过热",
            "price": 10.90,
            "pct_chg": 9.0,
            "volume": 40_000,
            "amount": 43_600_000,
            "today_open": 10.20,
            "avg_vol_20d": 1_000_000,
            "circ_mv": 1_090_000_000,
        },
        {
            "symbol": "000003",
            "name": "成交额不足",
            "price": 10.10,
            "pct_chg": 2.0,
            "volume": 4_000,
            "amount": 4_040_000,
            "today_open": 10.00,
            "avg_vol_20d": 100_000,
            "circ_mv": 1_010_000_000,
        },
    ])

    signals = filter_volume_shock_signals(df, {})

    assert signals["symbol"].tolist() == ["000001"]
    row = signals.iloc[0]
    assert row["vol_ratio_live"] == 4.0
    assert row["turnover_pct"] == 4.0
    assert "量能冲击观察" in volume_shock_buy_remark(row, {})
    metrics = volume_shock_signal_metrics(row, {"min_turnover_pct": 1.0, "max_turnover_pct": 12.0})
    assert metrics["turnover_pct"] == 4.0
    assert metrics["vol_ratio"] == 4.0
    assert metrics["param.min_turnover_pct"] == 1.0
    assert metrics["param.max_turnover_pct"] == 12.0
