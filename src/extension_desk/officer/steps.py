"""Graph steps in plain English, for the officer's page.

Each step is {"kind", "text"}: ask (the model asks a tool), data (records
came back), ok (a check passed), bad (a refusal or failure), gate (waiting
for the officer), meta (housekeeping). Derived from the same graph updates
the terminal trace prints; nothing here decides anything.
"""

import json
import re

from extension_desk.agent.graph import QUEUE_REFUSED, _text, parse_recommendations


def _step(kind: str, text: str) -> dict:
    return {"kind": kind, "text": text}


def _json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _crops(values) -> str:
    return ", ".join(dict.fromkeys(v for v in values if v)) or "none"


def ask(name: str, args: dict) -> str:
    args = args or {}
    if name == "get_plot_records":
        return f"Looking up {args.get('household_id', 'the household')}'s plots"
    if name == "get_rainfall":
        return f"Checking rainfall in {str(args.get('district', 'the district')).title()}"
    if name == "get_prices":
        return f"Checking the {args.get('crop', 'crop')} price"
    if name == "get_pest_alerts":
        return f"Checking pest alerts in {str(args.get('district', 'the district')).title()}"
    if name == "get_current_time":
        return "Checking today's date"
    if name == "submit_draft":
        recs, _ = parse_recommendations(args.get("recommendations", []))
        return f"Drafting advice: {len(recs)} line{'s' if len(recs) != 1 else ''}"
    if name == "no_advice":
        return "Declining to advise: the records don't support it"
    return f"Calling {name}"


def found(name: str, text: str) -> str:
    data = _json(text) or {}
    if name == "get_plot_records":
        hhs = data.get("households") or []
        if not hhs:
            return "No such household in the records"
        plots = [p for h in hhs for p in h.get("plots", [])]
        planted = sum(bool(p.get("planted_on")) for p in plots)
        return f"{len(plots)} plots: {_crops(p.get('crop') for p in plots)}; {planted} planted"
    if name == "get_rainfall":
        period = data.get("period") or {}
        if data.get("total_mm") is None:
            return "No rainfall records for that period"
        prelim = ", preliminary" if data.get("includes_preliminary") else ""
        return (f"Rain {period.get('start', '?')} to {period.get('end', '?')}: {data['total_mm']} mm, "
                f"normal {data.get('normal_mm', '?')} mm (CHIRPS{prelim})")
    if name == "get_prices":
        parts = []
        for s in data.get("series") or []:
            latest = s.get("latest") or {}
            srcs = " ".join(str(p.get("source", "")) for p in s.get("points") or [])
            who = "FEWS NET" if "FEWS NET" in srcs else "WFP" if "WFP" in srcs else "public data"
            unit = str(s.get("unit") or "USD/kg").replace("USD/", "")
            parts.append(f"{s.get('commodity')} at {s.get('market')}: ${latest.get('price_usd')}/{unit} ({who})")
        return "; ".join(parts) if parts else "No price records"
    if name == "get_pest_alerts":
        alerts = data.get("alerts") or []
        if not alerts:
            return "No pest alerts"
        return (f"{len(alerts)} pest alerts: {_crops(a.get('pest') for a in alerts)}, "
                f"all on {_crops(c for a in alerts for c in str(a.get('crops_affected', '')).split(';'))}")
    if name == "get_current_time":
        return "Got today's date"
    return f"{name} returned {len(data.get('citations', []))} records"


REFUSAL = re.compile(r"recommendation (\d+) \(")
WRONG_CROP = re.compile(r"affects ([\w ]+?), which (hh:\d+) doesn't grow \(([^)]*)\)")


def refusal(text: str) -> str:
    line = REFUSAL.search(text)
    crop = WRONG_CROP.search(text)
    if line and crop:
        return (f"Refused by our server: line {line.group(1)} cites pest alerts for {crop.group(1)}, "
                f"but {crop.group(2)} grows {crop.group(3)}")
    detail = " ".join(text.replace(QUEUE_REFUSED, "").split())[:160]
    return f"Refused by our server: {detail.lstrip(': ')}"


def describe(node: str, update: dict | None) -> list[dict]:
    update = update or {}
    out = []
    if node == "clock" and update.get("today"):
        out.append(_step("meta", f"Today is {update['today']} (from the time server)"))
    for msg in update.get("messages", []):
        kind = getattr(msg, "type", "")
        if kind == "ai":
            calls = getattr(msg, "tool_calls", []) or []
            for tc in calls:
                out.append(_step("ask", ask(tc["name"], tc.get("args"))))
            if not calls and _text(msg).strip():
                out.append(_step("meta", "The model wrote text instead of using a tool"))
        elif kind == "tool":
            text = _text(msg)
            if getattr(msg, "status", "") == "error":
                if node == "check":
                    out.append(_step("bad", "Check 1 refused the draft: a source didn't come from this run"))
                elif node == "nudge":
                    out.append(_step("meta", "Asked the model to call tools one at a time"))
                else:
                    out.append(_step("bad", f"{msg.name or node} failed: {' '.join(text.split())[:120]}"))
            elif node == "check":
                out.append(_step("ok", "Check 1 passed: every source came from this run"))
            elif msg.name and msg.name != "submit_draft":
                out.append(_step("data", found(msg.name, text)))
        elif kind == "human" and node == "queue_draft":
            out.append(_step("bad", refusal(_text(msg))))
        elif kind == "human" and node == "nudge":
            out.append(_step("meta", "Reminded the model to act through its tools"))
    if node == "queue_draft" and update.get("item"):
        out.append(_step("ok", "Check 2 passed: every source is about this household"))
        out.append(_step("gate", "Draft queued for the officer. Nothing has been sent"))
    outcome = update.get("outcome") or {}
    if node == "no_advice" and outcome:
        out.append(_step("meta", f"No advice drafted: {outcome.get('reason', '')[:160]}"))
    if node == "drop" and outcome:
        out.append(_step("bad", "Draft dropped after repeated refusals. Nothing reached the officer"))
    return out
