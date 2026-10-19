#!/usr/bin/env python3
"""每日大盘复盘入口：采集 → 触发门控 → 合成 → 落库 → 钉钉。

用法:
  .venv/bin/python scripts/review/daily_market_review.py
  .venv/bin/python scripts/review/daily_market_review.py --date 2026-10-16 --force
  .venv/bin/python scripts/review/daily_market_review.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_market_review as base  # noqa: E402
import review_synth as synth  # noqa: E402

OUT_DIR = ROOT / "research/daily_review"


def latest_trading_day(conn) -> str:
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) FROM daily_kline")
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else datetime.now().strftime("%Y-%m-%d")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="复盘交易日 YYYY-MM-DD，默认取 daily_kline 最新日")
    ap.add_argument("--force", action="store_true", help="忽略触发门控，强制产出")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不落库不推送")
    ap.add_argument("--no-ding", action="store_true", help="不推送钉钉")
    args = ap.parse_args()

    conn = base.db()
    trade_date = args.date or latest_trading_day(conn)
    base.log(f"复盘目标日 {trade_date}")

    # 幂等：同日已产出则跳过（--force 可覆盖）
    out_file = OUT_DIR / f"{trade_date}.json"
    if out_file.exists() and not args.force and not args.dry_run:
        base.log(f"{trade_date} 已存在复盘记录，跳过（--force 可重跑）")
        conn.close()
        return 0

    # 1) 采集
    tape = base.fetch_tape(conn, trade_date)
    if not tape.total:
        base.log(f"ERROR: {trade_date} 无 K 线数据（非交易日或同步未完成）")
        conn.close()
        return 2
    if not tape.index_pct:
        base.log("ERROR: 指数数据全部获取失败")
        conn.close()
        return 2

    baseline = base.fetch_zt_baseline(conn, trade_date)
    overseas = base.fetch_overseas(conn, trade_date)
    events = base.fetch_events(trade_date)
    etf = base.fetch_etf_flows(conn, trade_date)
    timeline = base.fetch_timeline(trade_date)
    intraday = base.fetch_intraday_path(trade_date) if not timeline else {}
    conn.close()

    # 数据新鲜度断言（防拿 T-2 数据判 T 日）
    if etf.get("stale"):
        base.log(f"[warn] ETF 份额数据停在 {etf.get('data_date')}，本次不复用")
        etf = {}

    # 2) 触发判定
    fired = base.evaluate_triggers(tape, baseline, overseas, events)
    base.log(f"触发信号 {len(fired)} 条：" + ("；".join(f["detail"] for f in fired) or "无"))
    if not fired and not args.force:
        base.log("未达触发阈值，静默退出（正常日不产复盘）")
        if not args.dry_run:
            # 记录"已检查"，避免小时级 launchd 在平静日反复采集
            synth.persist_quiet(trade_date)
        return 0

    # 3) 规则层 regime
    regime = synth.classify_regime(tape, fired)

    # 4) 合成
    bundle = synth.build_bundle(tape, baseline, overseas, events, etf, timeline,
                                regime, fired, intraday)
    try:
        llm_out = synth.call_llm(bundle)
    except Exception as e:  # noqa: BLE001
        base.log(f"[warn] 模型合成失败，降级为规则结论: {e}")
        llm_out = {
            "summary": f"{regime['regime']}：{regime['reason']}",
            "structure": f"上涨 {tape.adv} 家 / 下跌 {tape.dec} 家",
            "drivers": [], "volume_note": "", "next_risk": "",
            "confidence": "low",
        }
    llm_out, problems = synth.validate_llm(llm_out, bundle)

    record = {
        "date": trade_date,
        "rule_version": base.RULE_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "regime": regime,
        "triggers": fired,
        "tape": {
            "index_pct": tape.index_pct, "index_close": tape.index_close,
            "adv": tape.adv, "dec": tape.dec, "flat": tape.flat, "total": tape.total,
            "zt": tape.zt, "dt": tape.dt, "max_lianban": tape.max_lianban,
            "turnover_yi": tape.turnover_yi, "turnover_prev5_yi": tape.turnover_prev5_yi,
            "margin_balance_yi": tape.margin_balance_yi,
            "drawdown_60d_pct": tape.drawdown_60d_pct,
        },
        "overseas": overseas, "etf_flows": etf, "events": events,
        "timeline_size": len(timeline),
        "intraday_path": intraday,
        "data_gaps": bundle.get("data_gaps", []),
        "judgment": llm_out,
        "validation_problems": problems,
    }

    md = synth.render_markdown(record)
    if args.dry_run:
        print("\n" + "=" * 70)
        print(md)
        print("=" * 70)
        print(json.dumps(record, ensure_ascii=False, indent=2)[:2000])
        return 0

    path = synth.persist(record)
    md_path = path.with_suffix(".md")
    md_path.write_text(md, encoding="utf-8")
    base.log(f"已写入 {path} 与 {md_path}")

    if not args.no_ding:
        synth.ding_send(synth.ding_text(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
