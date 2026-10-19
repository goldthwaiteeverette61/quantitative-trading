#!/usr/bin/env python3
"""每日大盘复盘：信号触发 → 证据核对 → 结构化结论 → 落库 + 钉钉。

设计红线：
1. 只在触发日产出，平静日静默退出（避免低价值叙事堆积）。
2. 每个归因项必须带来源等级（A/B 事实 / C 卖方观点 / D 传闻），
   C/D 类不得作为原因陈述，只能标为"观点/未证实"。
3. 触发规则预注册冻结；改阈值必须走版本号变更 + 重新校准。
4. 复盘结论拆出机器可读字段（regime / sectors / confidence），
   供后续信号层使用；本版不写回任何交易信号。

用法:
  .venv/bin/python scripts/review/daily_market_review.py            # 自动判定当日
  .venv/bin/python scripts/review/daily_market_review.py --date 2026-10-16 --force
  .venv/bin/python scripts/review/daily_market_review.py --dry-run  # 只打印不落库不推送
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pymysql
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config.settings import DataConfig  # noqa: E402

# ── 预注册触发规则（v1，冻结）─────────────────────────────────────────────
RULE_VERSION = "review-v1"
TRIGGER = {
    "index_pct_abs": 1.5,        # 上证单日 |涨跌幅| ≥ 1.5%
    "breadth_ratio": 0.75,       # 单边占比（涨或跌）≥ 75%
    "limit_zt_sigma": 2.0,       # 涨停家数偏离 20 日均值 ≥ 2 倍标准差
    "limit_dt_abs": 40,          # 跌停家数 ≥ 40（绝对恐慌阈值）
    "overseas_pct_abs": 2.0,     # 隔夜纳指/标普 |涨跌幅| ≥ 2%
    "event_day": True,           # L0 日历事件日（美侧发布在 A 股收盘后）
}

INDEX_CODES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
}

ETF_UNIVERSE = ["510300", "510310", "510330", "510050", "510500", "510880", "512100", "588000"]
ETF_NAMES = {
    "510300": "华泰柏瑞300", "510310": "易方达300", "510330": "华夏300",
    "510050": "华夏50", "510500": "南方500", "510880": "红利ETF",
    "512100": "南方1000", "588000": "科创50ETF",
}

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")}
SINA_FEED = "https://zhibo.sina.com.cn/api/zhibo/feed"
DING_ENV = Path("/opt/mywork/data/dingtalk.env")
OUT_DIR = ROOT / "research/daily_review"
STATE_FILE = OUT_DIR / "state.json"
JUDGMENT_LOG = OUT_DIR / "judgments.jsonl"


# ── 基础工具 ────────────────────────────────────────────────────────────

def db():
    c = DataConfig()
    return pymysql.connect(host=c.mysql_host, port=c.mysql_port, user=c.mysql_user,
                           password=c.mysql_password, database=c.mysql_database,
                           charset="utf8mb4")


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def http_json(url: str, params: dict | None = None, timeout: int = 20):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def http_text(url: str, timeout: int = 20) -> str:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


# ── 数据采集层 ──────────────────────────────────────────────────────────

@dataclass
class Tape:
    trade_date: str
    index_pct: dict = field(default_factory=dict)      # {code: pct}
    index_close: dict = field(default_factory=dict)
    adv: int = 0
    dec: int = 0
    flat: int = 0
    total: int = 0
    zt: int | None = None
    dt: int | None = None
    max_lianban: int | None = None
    turnover_yi: float | None = None
    turnover_prev5_yi: list = field(default_factory=list)
    margin_balance_yi: float | None = None
    margin_date: str | None = None
    drawdown_60d_pct: float | None = None


def compute_limits(conn, trade_date: str) -> tuple[int | None, int | None, int | None]:
    """本地现算涨停/跌停/最高连板。

    口径对齐 scripts/fetch_market_sentiment.py：
    - 涨跌停价 = round(pre_close × (1±r), 2) 四舍五入到分，收盘价与之相差 <0.002 视为封板
    - 主板 10% / ST 5% / 创业板·科创板 20% / 北交所 920 30%
    - 上市 7 个自然日内不判定（新股）
    """
    import numpy as np
    import pandas as pd
    from decimal import Decimal, ROUND_HALF_UP

    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, date, close, pre_close, is_st
        FROM daily_kline
        WHERE date BETWEEN DATE_SUB(%s, INTERVAL 40 DAY) AND %s
          AND pre_close > 0
          AND (symbol LIKE '0%%' OR symbol LIKE '3%%' OR symbol LIKE '6%%'
               OR symbol LIKE '92%%')
        ORDER BY symbol, date
    """, (trade_date, trade_date))
    rows = cur.fetchall()
    if not rows:
        return None, None, None

    cur.execute("SELECT symbol, ipo_date FROM stock_basic WHERE ipo_date IS NOT NULL")
    ipo = {s: d for s, d in cur.fetchall()}

    df = pd.DataFrame(rows, columns=["symbol", "date", "close", "pre_close", "is_st"])

    def limit_price(pre: float, pct: float, up: bool) -> float:
        d = Decimal(str(pre)) * (Decimal("1") + Decimal(str(pct)) / 100
                                 if up else Decimal("1") - Decimal(str(pct)) / 100)
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def board_pct(sym: str, st: int) -> float:
        if sym.startswith("68") or sym.startswith("30"):
            return 20.0
        if sym.startswith("92"):
            return 30.0
        if st:
            return 5.0
        return 10.0

    pre = df["pre_close"].astype(float).to_numpy()
    close = df["close"].astype(float).to_numpy()
    st = df["is_st"].fillna(0).astype(int).to_numpy()
    syms = df["symbol"].to_numpy()
    pcts = np.array([board_pct(s, x) for s, x in zip(syms, st)])
    up_p = np.array([limit_price(p, r, True) for p, r in zip(pre, pcts)])
    dn_p = np.array([limit_price(p, r, False) for p, r in zip(pre, pcts)])

    dates = [str(x) for x in df["date"].astype(str).to_numpy()]
    ipo_arr = [str(ipo.get(s, "") or "") for s in syms]
    fresh = np.array([
        bool(d0) and bool(d1) and (pd.Timestamp(d0) - pd.Timestamp(d1)).days <= 7
        for d0, d1 in zip(dates, ipo_arr)
    ])
    df["is_zt"] = (np.abs(close - up_p) < 0.002) & ~fresh
    df["is_dt"] = (np.abs(close - dn_p) < 0.002) & ~fresh

    df["pos"] = df.groupby("date").ngroup()
    g = df.groupby("symbol", sort=False)
    df["prev_zt"] = g["is_zt"].shift(1).fillna(False)
    df["prev_pos"] = g["pos"].shift(1)
    df["cont"] = df["is_zt"] & df["prev_zt"] & ((df["pos"] - df["prev_pos"]) == 1)
    df["sid"] = np.where(df["is_zt"] & ~df["cont"], 1, 0)
    df["sid"] = df.groupby("symbol", sort=False)["sid"].cumsum()
    df["streak"] = df.groupby(["symbol", "sid"], sort=False)["is_zt"].cumsum()

    day = df[df["date"].astype(str) == trade_date]
    if day.empty:
        return None, None, None
    zt = int(day["is_zt"].sum())
    dt = int(day["is_dt"].sum())
    mlb = int(day.loc[day["is_zt"], "streak"].max()) if zt else 0
    return zt, dt, mlb


def fetch_tape(conn, trade_date: str) -> Tape:
    """盘面事实：指数涨跌幅 + 广度 + 涨停跌停 + 量能 + 两融。

    涨跌停/连板在本地 daily_kline 上现算，不依赖 market_sentiment 表
    （该表由另一 agent 维护，实测存在滞后，依赖它会把 null 写进结论）。
    """
    t = Tape(trade_date=trade_date)
    cur = conn.cursor()

    # 指数：新浪日线（前复权无关，指数无除权）
    for code, name in INDEX_CODES.items():
        try:
            raw = http_text(
                "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
                f"?symbol={code}&scale=240&ma=no&datalen=70"
            )
            bars = json.loads(raw)
            rows = [(b["day"], float(b["close"])) for b in bars]
            # 丢弃晚于目标日的 bar（盘中运行时当日 bar 仍在累积；收盘后当日 bar 已完整）
            rows = [r for r in rows if r[0] <= trade_date]
            if not rows or rows[-1][0] != trade_date:
                continue
            close = rows[-1][1]
            prev = rows[-2][1] if len(rows) > 1 else close
            t.index_pct[code] = round((close / prev - 1) * 100, 2)
            t.index_close[code] = round(close, 2)
            if code == "sh000001" and len(rows) >= 60:
                hi60 = max(c for _, c in rows[-60:])
                t.drawdown_60d_pct = round((close / hi60 - 1) * 100, 2)
        except Exception as e:  # noqa: BLE001
            log(f"[warn] 指数 {code} 获取失败: {e}")

    # 广度 + 量能：本地 daily_kline（单日单次扫描）
    cur.execute("""
        SELECT COUNT(*) n,
               SUM(close > pre_close) adv_n,
               SUM(close < pre_close) dec_n,
               SUM(close = pre_close) flat_n,
               ROUND(SUM(amount)/1e8, 1) amt_yi
        FROM daily_kline
        WHERE date = %s AND pre_close > 0
          AND (symbol LIKE '0%%' OR symbol LIKE '3%%' OR symbol LIKE '6%%')
    """, (trade_date,))
    row = cur.fetchone()
    if row and row[0]:
        t.total, t.adv, t.dec, t.flat = int(row[0]), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
        t.turnover_yi = float(row[4]) if row[4] else None

    # 近 5 日量能（对比用）
    cur.execute("""
        SELECT date, ROUND(SUM(amount)/1e8, 1) amt
        FROM daily_kline
        WHERE date < %s AND pre_close > 0
          AND (symbol LIKE '0%%' OR symbol LIKE '3%%' OR symbol LIKE '6%%')
        GROUP BY date ORDER BY date DESC LIMIT 5
    """, (trade_date,))
    t.turnover_prev5_yi = [float(r[1]) for r in cur.fetchall() if r[1]]

    # 涨跌停 + 连板：本地现算（口径对齐 fetch_market_sentiment.py）
    t.zt, t.dt, t.max_lianban = compute_limits(conn, trade_date)
    # 两融余额：情绪表可能滞后，取不晚于目标日的最近一行并标注日期
    cur.execute("""SELECT trade_date, margin_balance FROM market_sentiment
                   WHERE trade_date <= %s AND margin_balance IS NOT NULL
                   ORDER BY trade_date DESC LIMIT 1""", (trade_date,))
    r = cur.fetchone()
    if r:
        t.margin_balance_yi = round(float(r[1]) / 1e8, 1)
        t.margin_date = str(r[0])
    return t


def fetch_zt_baseline(conn, trade_date: str) -> dict:
    """涨停家数 20 日均值与标准差（不含当日），用于 z 值判定。"""
    cur = conn.cursor()
    cur.execute("""
        SELECT zt_count FROM market_sentiment
        WHERE trade_date < %s ORDER BY trade_date DESC LIMIT 20
    """, (trade_date,))
    vals = [int(r[0]) for r in cur.fetchall() if r[0] is not None]
    if len(vals) < 10:
        return {"n": len(vals), "mean": None, "std": None, "z": None}
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    return {"n": len(vals), "mean": round(mean, 1), "std": round(std, 2), "z": None}


def fetch_overseas(conn, trade_date: str) -> dict:
    """隔夜外盘：取 snap_date 不晚于目标日的最近一行，并显式标注陈旧天数。

    macro_overseas_daily 由另一 agent 维护，实测会滞后；不标注就会把
    T-3 的外盘当 T 日隔夜数据写进结论。
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT snap_date, us_session_date, spx_close, ndx_close, dji_close,
               vix_close, us_10y_yield, usdcny_fix
        FROM macro_overseas_daily
        WHERE snap_date <= %s ORDER BY snap_date DESC LIMIT 1
    """, (trade_date,))
    r = cur.fetchone()
    if not r:
        return {}
    snap = str(r[0])
    lag_days = (datetime.strptime(trade_date, "%Y-%m-%d") -
                datetime.strptime(snap, "%Y-%m-%d")).days
    out = {"snap_date": snap, "lag_days": lag_days,
           "stale": lag_days > 0,
           "vix": float(r[5]) if r[5] else None,
           "us_10y": float(r[6]) if r[6] else None,
           "usdcny": float(r[7]) if r[7] else None}
    cur.execute("""
        SELECT spx_close, ndx_close FROM macro_overseas_daily
        WHERE us_session_date < %s ORDER BY us_session_date DESC LIMIT 1
    """, (str(r[1]),))
    p = cur.fetchone()
    if p:
        for key, now_v, prev_v in (("spx", r[2], p[0]), ("ndx", r[3], p[1])):
            if now_v and prev_v:
                out[f"{key}_pct"] = round((float(now_v) / float(prev_v) - 1) * 100, 2)
    return out


def fetch_events(trade_date: str) -> dict:
    """L0 事件日历：当日是否有美侧数据发布（A 股收盘后才发生，属次日风险点）。"""
    p = ROOT / "research/macro_calendar.csv"
    if not p.exists():
        return {"today": [], "next": []}
    today, nxt = [], []
    for line in p.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        ev, ref, us_d, us_t, bj = parts[0], parts[1], parts[2], parts[3], parts[4]
        bj_date = bj.split(" ")[0]
        item = {"event": ev, "ref": ref, "bj_datetime": bj}
        if bj_date == trade_date:
            today.append(item)
        elif bj_date > trade_date and len(nxt) < 3:
            nxt.append(item)
    return {"today": today, "next": nxt}


def fetch_etf_flows(conn, trade_date: str) -> dict:
    """宽基 ETF 份额变化 × 收盘价 = 净申赎金额（国家队签名判据）。"""
    p = ROOT / "research/national_team/share_history.csv"
    if not p.exists():
        return {}
    import pandas as pd
    df = pd.read_csv(p, dtype={"date": str, "code": str})
    # CSV 日期为 YYYYMMDD，目标日为 YYYY-MM-DD，先统一成 YYYYMMDD 再比较
    key = trade_date.replace("-", "")
    df = df[df["date"].str.replace("-", "", regex=False) <= key].copy()
    if df.empty:
        return {}
    dates = sorted(df["date"].str.replace("-", "", regex=False).unique())
    if key not in dates or len(dates) < 2:
        latest = dates[-1] if dates else None
        return {"data_date": latest, "stale": True}
    prev_key = dates[-2]
    cur_rows = df[df["date"].str.replace("-", "", regex=False) == key].set_index("code")["shares"]
    prev_rows = df[df["date"].str.replace("-", "", regex=False) == prev_key].set_index("code")["shares"]

    # 收盘价：本地 daily_kline 的 ETF 行可能缺失，用腾讯批量快照兜底
    closes = {}
    try:
        q = ",".join(f"sh{c}" for c in ETF_UNIVERSE)
        raw = http_text(f"http://qt.gtimg.cn/q={q}")
        for line in raw.strip().split("\n"):
            if not line.strip():
                continue
            code = line.split("=")[0].replace("v_", "")[2:]
            parts = line.split('"')[1].split("~")
            closes[code] = float(parts[3])
    except Exception as e:  # noqa: BLE001
        log(f"[warn] ETF 快照失败: {e}")

    flows = {}
    for code in ETF_UNIVERSE:
        if code not in cur_rows.index or code not in prev_rows.index:
            continue
        d_share = float(cur_rows[code]) - float(prev_rows[code])
        price = closes.get(code)
        if price is None:
            continue
        flows[code] = {
            "name": ETF_NAMES.get(code, code),
            "delta_shares_yi": round(d_share / 1e8, 2),
            "amount_yi": round(d_share * price / 1e8, 2),
        }
    total = round(sum(v["amount_yi"] for v in flows.values()), 1) if flows else None
    return {"data_date": key, "prev_date": prev_key, "flows": flows,
            "total_amount_yi": total}


def fetch_timeline(trade_date: str) -> list:
    """新浪 7x24 带时间戳的大盘稿件：重建开评→午评→收评轨迹。

    7x24 feed 实测只回溯约 1.5 天，历史日期拿不到；此时退回指数分钟线
    重建轨迹（无叙事，只有价格路径），并在返回中显式标注缺失。
    """
    kws = ("沪指", "上证", "大盘", "创业板", "科创", "两市", "成交额",
           "涨停", "跌停", "板块", "主力", "净流")
    lo = f"{trade_date} 09:00:00"
    hi = f"{trade_date} 16:30:00"
    out, page, guard = [], 1, 0
    while guard < 40:
        guard += 1
        try:
            data = http_json(SINA_FEED, params={
                "page": page, "page_size": 100, "zhibo_id": 152,
                "tag_id": 0, "dire": "f", "dpc": 1,
            })
            feeds = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
        except Exception as e:  # noqa: BLE001
            log(f"[warn] 7x24 page={page} 失败: {e}")
            break
        if not feeds:
            break
        oldest = None
        for x in feeds:
            ts = x.get("create_time", "")
            oldest = min(oldest, ts) if oldest else ts
            if lo <= ts <= hi:
                text = strip_html(x.get("rich_text", ""))
                if any(k in text for k in kws):
                    out.append({"time": ts, "text": text})
        if oldest and oldest < lo:
            break
        page += 1
    return out


def fetch_intraday_path(trade_date: str) -> dict:
    """指数分钟线重建日内轨迹：开盘/午间/尾盘三段 + 全天形态判定。

    替代 7x24 在历史日期上的缺失。只有价格路径，不含新闻叙事，
    调用方必须据此标注「时间线缺失·仅价格路径」。
    """
    out = {}
    for code, name in (("sh000001", "上证"), ("sz399006", "创业板")):
        try:
            raw = http_text(
                "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
                f"?symbol={code}&scale=5&ma=no&datalen=400"
            )
            bars = [b for b in json.loads(raw) if b["day"].startswith(trade_date)]
            if not bars:
                continue
            closes = [float(b["close"]) for b in bars]
            first, midday, last = closes[0], closes[len(closes) // 2], closes[-1]
            high, low = max(closes), min(closes)
            out[name] = {
                "open": round(first, 2),
                "midday": round(midday, 2),
                "close": round(last, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "bars": len(closes),
                # 形态：午后跌幅是否显著大于上午
                "pm_selloff": (last / midday - 1) * 100 < -0.3,
                "morning_drop": (midday / first - 1) * 100 < -0.5,
            }
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {name} 分钟线失败: {e}")
    return out


# ── 触发判定 ────────────────────────────────────────────────────────────

def evaluate_triggers(tape: Tape, baseline: dict, overseas: dict, events: dict) -> list:
    fired = []
    pct = tape.index_pct.get("sh000001")
    if pct is not None and abs(pct) >= TRIGGER["index_pct_abs"]:
        fired.append({"rule": "index_pct_abs", "value": pct,
                      "threshold": TRIGGER["index_pct_abs"],
                      "detail": f"上证 {pct:+.2f}%"})
    if tape.total:
        up_ratio = tape.adv / tape.total
        dn_ratio = tape.dec / tape.total
        if max(up_ratio, dn_ratio) >= TRIGGER["breadth_ratio"]:
            side = "涨" if up_ratio > dn_ratio else "跌"
            fired.append({"rule": "breadth_ratio", "value": round(max(up_ratio, dn_ratio), 3),
                          "threshold": TRIGGER["breadth_ratio"],
                          "detail": f"{side}家数占比 {max(up_ratio, dn_ratio):.1%}"})
    if tape.zt is not None and baseline.get("std"):
        z = (tape.zt - baseline["mean"]) / baseline["std"]
        if abs(z) >= TRIGGER["limit_zt_sigma"]:
            fired.append({"rule": "limit_zt_sigma", "value": round(z, 2),
                          "threshold": TRIGGER["limit_zt_sigma"],
                          "detail": f"涨停 {tape.zt} 家，20日均 {baseline['mean']}（z={z:+.1f}）"})
    if tape.dt is not None and tape.dt >= TRIGGER["limit_dt_abs"]:
        fired.append({"rule": "limit_dt_abs", "value": tape.dt,
                      "threshold": TRIGGER["limit_dt_abs"],
                      "detail": f"跌停 {tape.dt} 家"})
    for key in ("ndx_pct", "spx_pct"):
        v = overseas.get(key)
        if v is not None and abs(v) >= TRIGGER["overseas_pct_abs"]:
            fired.append({"rule": "overseas_pct_abs", "value": v,
                          "threshold": TRIGGER["overseas_pct_abs"],
                          "detail": f"{'纳指' if key == 'ndx_pct' else '标普'} {v:+.2f}%"})
    if TRIGGER["event_day"] and events.get("today"):
        fired.append({"rule": "event_day", "value": len(events["today"]),
                      "threshold": 1,
                      "detail": "、".join(e["event"] for e in events["today"])})
    return fired
