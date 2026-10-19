#!/usr/bin/env python3
"""每日大盘复盘 —— 合成 / 落库 / 推送层（由 daily_market_review.py 导入）。"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "research/daily_review"
LLM_ENDPOINT = "http://127.0.0.1:8770/v1/chat/completions"
LLM_MODEL = "qwen3.8-27b-fp8"
DING_ENV = Path("/opt/mywork/data/dingtalk.env")
JUDGMENT_LOG = OUT_DIR / "judgments.jsonl"

GRADES = ("A", "B", "C", "D")
GRADE_LABEL = {"A": "官方/交易所", "B": "权威媒体交叉", "C": "卖方观点", "D": "传闻·未证实"}


# ── 规则层：regime 判定（不交给模型，可复现）───────────────────────────────

def classify_regime(tape, fired: list) -> dict:
    """regime 由固定规则给出，模型只负责叙事，避免同一数据两次跑出两个标签。"""
    pct = tape.index_pct.get("sh000001")
    up_ratio = tape.adv / tape.total if tape.total else None
    dn_ratio = tape.dec / tape.total if tape.total else None
    cyb = tape.index_pct.get("sz399006")
    kc50 = tape.index_pct.get("sh000688")
    hs300 = tape.index_pct.get("sh000300")

    spread = None
    if pct is not None:
        peers = [v for v in (cyb, kc50, hs300) if v is not None]
        if peers:
            spread = round(max([pct] + peers) - min([pct] + peers), 2)

    regime, reason = "normal", "无显著异动"
    if tape.dt is not None and tape.dt >= 40:
        regime, reason = "panic_selloff", f"跌停 {tape.dt} 家（≥40 绝对恐慌线）"
    elif pct is not None and pct <= -1.5 and dn_ratio is not None and dn_ratio >= 0.8:
        regime, reason = "panic_selloff", f"上证 {pct:+.2f}% 且下跌家数占比 {dn_ratio:.0%}"
    elif pct is not None and pct >= 1.5 and up_ratio is not None and up_ratio >= 0.8:
        regime, reason = "broad_rally", f"上证 {pct:+.2f}% 且上涨家数占比 {up_ratio:.0%}"
    elif spread is not None and spread >= 2.0 and pct is not None and abs(pct) < 1.5:
        regime = "structural_divergence"
        reason = f"指数间最大分化 {spread:.2f}pp（沪指 {pct:+.2f}%），非全面行情"
    elif any(f["rule"] == "event_day" for f in fired):
        regime, reason = "event_driven", "宏观事件日驱动"

    return {"regime": regime, "reason": reason, "index_spread_pp": spread,
            "up_ratio": round(up_ratio, 3) if up_ratio is not None else None,
            "dn_ratio": round(dn_ratio, 3) if dn_ratio is not None else None}


# ── 模型层：叙事合成 ────────────────────────────────────────────────────

SYSTEM = """你是A股盘后复盘分析器。输入是一天的客观数据包（指数、广度、涨跌停、量能、外盘、ETF份额流、带时间戳的快讯、日内价格路径）。
输出严格 JSON，不要解释、不要 Markdown 围栏。字段：
{
 "summary": "一句话结论（≤60字，只说事实与结构，不预测）",
 "structure": "结构特征（≤80字：谁领跌/领涨、是普跌还是分化）",
 "drivers": [{"text":"归因项（≤50字）","grade":"A|B|C|D","evidence":"引用输入中的具体数字或快讯原文片段"}],
 "volume_note": "量能背景（≤40字，对比近5日均值）",
 "next_risk": "下一风险点（≤50字，来自事件日历）",
 "confidence": "high|medium|low"
}
硬性约束：
1. drivers 是"为什么会这样"，不是"发生了什么"的复述。
   - 禁止把指数涨跌幅、涨跌家数、涨跌停家数、成交额、日内路径这些**行情结果**写成 drivers。
   - 行情结果只能进 summary / structure / volume_note。
   - drivers 必须来自数据包中独立于当日行情的信息源：带时间戳的快讯（政策、事件、外盘、板块消息）、隔夜外盘涨跌、ETF 份额流、事件日历。
2. drivers 最多 4 条，每条必须有 grade 和 evidence；evidence 必须能在输入数据包中逐字找到，不得编造。
3. grade 含义：A=交易所/官方数据，B=权威媒体快讯交叉，C=卖方观点/机构解读，D=传闻。
4. C/D 类只能作为"市场解读"列出，不得写成事实原因；D 类必须在 text 里带"（未证实）"。
5. **找不到可验证的驱动因素就返回空 drivers 数组**，宁可空也不要拿行情数字充数。数据缺口多（data_gaps 非空）时 confidence 不得超过 medium。
6. 不预测涨跌、不给点位目标、不推荐个股。
7. 若 timeline_available=false，说明没有新闻源，drivers 通常应为空数组。"""


def build_bundle(tape, baseline, overseas, events, etf, timeline, regime, fired,
                 intraday=None) -> dict:
    timeline_ok = bool(timeline)
    return {
        "trade_date": tape.trade_date,
        "index_pct": tape.index_pct,
        "breadth": {"adv": tape.adv, "dec": tape.dec, "flat": tape.flat,
                    "total": tape.total, "up_ratio": regime["up_ratio"],
                    "dn_ratio": regime["dn_ratio"]},
        "limits": {"zt": tape.zt, "dt": tape.dt, "max_lianban": tape.max_lianban,
                   "zt_baseline_20d": baseline},
        "turnover_yi": tape.turnover_yi,
        "turnover_prev5_yi": tape.turnover_prev5_yi,
        "margin_balance_yi": tape.margin_balance_yi,
        "margin_date": tape.margin_date,
        "drawdown_60d_pct": tape.drawdown_60d_pct,
        "overseas": overseas,
        "etf_flows": etf,
        "events": events,
        "regime_rule": regime,
        "triggers": fired,
        "timeline_available": timeline_ok,
        "timeline": [f"{x['time'][11:16]} {x['text'][:160]}" for x in timeline[:40]],
        "intraday_path": intraday or {},
        "data_gaps": [
            gap for gap, missing in (
                ("新闻时间线（7x24 仅回溯约1.5天）", not timeline_ok),
                ("隔夜外盘（宏观表滞后）", bool(overseas.get("stale"))),
                ("两融余额（情绪表滞后）", tape.margin_date not in (None, tape.trade_date)),
                ("ETF 份额", not etf.get("flows")),
            ) if missing
        ],
    }


def call_llm(bundle: dict, timeout: int = 120) -> dict:
    payload = {
        "model": LLM_MODEL, "temperature": 0.1, "stream": True, "max_tokens": 1600,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(bundle, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        LLM_ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    chunks = []
    with opener.open(req, timeout=timeout) as resp:
        for line in resp:
            if not line.startswith(b"data: "):
                continue
            data = line[6:].strip()
            if data == b"[DONE]":
                break
            delta = json.loads(data)["choices"][0].get("delta", {})
            chunks.append(delta.get("content") or "")
    content = "".join(chunks).strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    return json.loads(content)


def validate_llm(out: dict, bundle: dict) -> tuple[dict, list]:
    """校验：字段齐全、grade 合法、evidence 可溯源、drivers 不得复述行情结果。"""
    problems = []
    if not isinstance(out, dict):
        raise ValueError("模型输出不是对象")

    corpus = json.dumps(bundle, ensure_ascii=False)
    for key in ("summary", "structure", "volume_note", "next_risk"):
        if not isinstance(out.get(key), str):
            out[key] = ""
            problems.append(f"缺字段 {key}")

    # 当日行情数字集合：drivers 的证据若只由这些数字构成，说明在复述结果
    tape_numbers = set()
    for v in (bundle.get("index_pct") or {}).values():
        tape_numbers.add(f"{abs(float(v)):.2f}")
    b = bundle.get("breadth") or {}
    for k in ("adv", "dec", "flat", "total"):
        if b.get(k) is not None:
            tape_numbers.add(str(b[k]))
    lim = bundle.get("limits") or {}
    for k in ("zt", "dt", "max_lianban"):
        if lim.get(k) is not None:
            tape_numbers.add(str(lim[k]))
    for k in ("turnover_yi",):
        if bundle.get(k) is not None:
            tape_numbers.add(str(bundle[k]))

    drivers = []
    for d in (out.get("drivers") or [])[:4]:
        if not isinstance(d, dict):
            problems.append("driver 非对象")
            continue
        text, grade, ev = (d.get("text") or "").strip(), (d.get("grade") or "").strip().upper(), (d.get("evidence") or "").strip()
        if not text or grade not in GRADES or not ev:
            problems.append(f"driver 字段不全: {text[:20]}")
            continue
        digits = re.findall(r"\d+\.?\d*", ev)
        if digits and not any(x in corpus for x in digits[:3]):
            problems.append(f"evidence 无法溯源: {ev[:30]}")
            continue
        # 反复述：证据里的数字全部来自当日行情 → 这是"发生了什么"，不是"为什么"
        if digits and all(x in tape_numbers for x in digits):
            problems.append(f"driver 复述行情结果，已剔除: {text[:24]}")
            continue
        if grade == "D" and "未证实" not in text:
            text = text + "（未证实）"
        drivers.append({"text": text, "grade": grade,
                        "grade_label": GRADE_LABEL[grade], "evidence": ev})
    out["drivers"] = drivers

    if out.get("confidence") not in ("high", "medium", "low"):
        out["confidence"] = "low"
        problems.append("confidence 非法")
    # 数据缺口存在时不得声称高置信
    if (bundle.get("data_gaps") or []) and out["confidence"] == "high":
        out["confidence"] = "medium"
        problems.append("存在数据缺口，置信度由 high 下调为 medium")
    return out, problems


# ── 渲染 / 落库 / 推送 ──────────────────────────────────────────────────

def render_markdown(record: dict) -> str:
    t, tape, reg, llm = record["date"], record["tape"], record["regime"], record["judgment"]
    lines = [f"# {t} 盘后复盘（{record['rule_version']}）", ""]

    idx = " | ".join(f"{name} {tape['index_pct'].get(code, 0):+.2f}%"
                     for code, name in (("sh000001", "上证"), ("sz399001", "深成"),
                                        ("sz399006", "创业板"), ("sh000688", "科创50")))
    lines += ["## 盘面事实", f"- 指数：{idx}",
              f"- 广度：涨 {tape['adv']} / 跌 {tape['dec']} / 平 {tape['flat']}（共 {tape['total']}）",
              f"- 涨跌停：涨停 {tape['zt']} / 跌停 {tape['dt']} / 最高 {tape['max_lianban']} 连板",
              f"- 量能：{tape['turnover_yi']} 亿（近5日均 "
              f"{round(sum(tape['turnover_prev5_yi'])/len(tape['turnover_prev5_yi']), 1) if tape['turnover_prev5_yi'] else '—'} 亿）",
              f"- 两融余额：{tape['margin_balance_yi']} 亿"
              if tape.get("margin_balance_yi") else "- 两融余额：—"]
    if tape.get("drawdown_60d_pct") is not None:
        lines.append(f"- 距 60 日高点：{tape['drawdown_60d_pct']:+.2f}%")

    lines += ["", "## 触发信号", *[f"- {f['detail']}（规则 {f['rule']}）" for f in record["triggers"]]]
    lines += ["", "## 结论", f"- 定性：{reg['regime']} —— {reg['reason']}",
              f"- {llm.get('summary', '')}"]
    if llm.get("structure"):
        lines.append(f"- 结构：{llm['structure']}")
    if llm.get("volume_note"):
        lines.append(f"- 量能：{llm['volume_note']}")
    lines += ["", "## 归因（分级）"]
    if llm["drivers"]:
        for d in llm["drivers"]:
            lines.append(f"- [{d['grade']}·{d['grade_label']}] {d['text']}｜证据：{d['evidence']}")
    else:
        lines.append("- 无可验证驱动因素（模型未找到可溯源证据）")

    etf = record.get("etf_flows") or {}
    if etf.get("flows"):
        top = sorted(etf["flows"].items(), key=lambda kv: -abs(kv[1]["amount_yi"]))[:3]
        lines += ["", "## 宽基 ETF 净申赎（国家队签名判据）"]
        lines.append(f"- 合计 {etf.get('total_amount_yi')} 亿元（{etf.get('prev_date')}→{etf.get('data_date')}）")
        for code, v in top:
            lines.append(f"- {v['name']}（{code}）{v['amount_yi']:+.1f} 亿元 / {v['delta_shares_yi']:+.2f} 亿份")

    if llm.get("next_risk"):
        lines += ["", "## 下一风险点", f"- {llm['next_risk']}"]

    gaps = record.get("data_gaps") or []
    if gaps:
        lines += ["", "## 数据缺口（结论置信度已相应下调）", *[f"- {g}" for g in gaps]]
    if record.get("validation_problems"):
        lines += ["", "## 校验告警", *[f"- {p}" for p in record["validation_problems"]]]
    lines += ["", f"_置信度：{llm.get('confidence')}｜规则版本：{record['rule_version']}_"]
    return "\n".join(lines)


def ding_send(content: str) -> None:
    import requests
    cfg = {}
    if DING_ENV.exists():
        for line in DING_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    keys = ("DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET",
            "DINGTALK_ROBOT_CODE", "DINGTALK_USER_ID")
    if not all(cfg.get(k) for k in keys):
        print("[ding] 凭据缺失，跳过", flush=True)
        return
    try:
        r = requests.post("https://api.dingtalk.com/v1.0/oauth2/accessToken",
                          json={"appKey": cfg["DINGTALK_CLIENT_ID"],
                                "appSecret": cfg["DINGTALK_CLIENT_SECRET"]}, timeout=20)
        token = r.json().get("accessToken")
        if not token:
            raise RuntimeError(f"token 获取失败: {r.text[:120]}")
        requests.post("https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                      headers={"x-acs-dingtalk-access-token": token,
                               "Content-Type": "application/json"},
                      json={"robotCode": cfg["DINGTALK_ROBOT_CODE"],
                            "userIds": [cfg["DINGTALK_USER_ID"]],
                            "msgKey": "sampleText",
                            "msgParam": json.dumps({"content": content}, ensure_ascii=False)},
                      timeout=20)
        print("[ding] 已发送", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[ding] 失败: {e}", flush=True)


def ding_text(record: dict) -> str:
    tape, reg, llm = record["tape"], record["regime"], record["judgment"]
    pct = tape["index_pct"].get("sh000001")
    head = f"【{record['date']} 复盘】上证 {pct:+.2f}%｜{reg['regime']}"
    body = [head, llm.get("summary", "")]
    if llm.get("structure"):
        body.append(f"结构：{llm['structure']}")
    for d in llm["drivers"][:3]:
        body.append(f"[{d['grade']}] {d['text']}")
    if llm.get("next_risk"):
        body.append(f"下一风险：{llm['next_risk']}")
    body.append(f"触发：{'；'.join(f['detail'] for f in record['triggers'][:3])}")
    return "\n".join(x for x in body if x)


def persist_quiet(trade_date: str) -> None:
    """平静日：只写"已检查"标记，不产复盘文件。

    没有这一步，小时级 launchd 会在每个 tick 重复采集一整天数据。
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = {"last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "last_date": trade_date, "last_result": "no_trigger"}
    (OUT_DIR / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                        encoding="utf-8")


def persist(record: dict, dry_run: bool = False) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"{record['date']}.json"
    if dry_run:
        print(f"[dry-run] 将写入 {p}")
        return p
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    with JUDGMENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: record[k] for k in
                            ("date", "rule_version", "regime", "triggers",
                             "tape", "judgment")}, ensure_ascii=False) + "\n")
    state = {"last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "last_date": record["date"], "last_result": "reviewed"}
    (OUT_DIR / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return p
