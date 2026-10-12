"""`uv run extension-desk demo`: one backlog pass over a pilot district.

For each household in the backlog the agent gathers evidence, drafts, and
stops at the officer approval gate. You answer as the officer. Nothing is
sent to any household; approved drafts wait in the review queue.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import date

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from extension_desk.agent.audit import AuditLog
from extension_desk.agent.graph import TIMEZONE, _text, build_graph
from extension_desk.agent.models import ModelUnavailable, make_model, model_details, model_spec
from extension_desk.agent.transcript import Transcript
from extension_desk.data import DATA_ENV, DataStore
from extension_desk.districts import DISTRICTS
from extension_desk.review_queue import ReviewQueue


def mcp_connections(queue: ReviewQueue, data_dir=None) -> dict:
    """Our server, plus the reference `time` server we didn't write.
    `data_dir` points our server at an eval fixture instead of data/."""
    env = {**os.environ, "EXTENSION_DESK_QUEUE": str(queue.path)}
    if data_dir:
        env[DATA_ENV] = str(data_dir)
    return {
        "extension_desk": {
            "transport": "stdio", "command": sys.executable,
            "args": ["-m", "extension_desk.mcp_server"],
            "env": env,
        },
        "time": {
            "transport": "stdio", "command": sys.executable,
            "args": ["-m", "mcp_server_time", "--local-timezone", TIMEZONE],
        },
    }


def backlog(store: DataStore, district: str, limit: int, today: date | None = None) -> list[str]:
    """Households most in need of a visit first: crops in the ground with a
    pest alert, then any planted plot, then longest since an officer visit."""
    today = today or date.today()
    pest_crops = {c for a in store.pest_alerts(district) for c in a["crops_affected"].split(";")}

    def score(hh: dict) -> tuple:
        plots = store.plots(hh["record_id"])
        planted = {p["crop"] for p in plots if p["planted_on"]}
        days = (today - date.fromisoformat(hh["last_officer_visit"])).days
        return (-(bool(planted & pest_crops)), -bool(planted), -days, hh["record_id"])

    ranked = [hh["record_id"] for hh in sorted(store.households(district), key=score)]
    return ranked[:limit] if limit else ranked


async def today_from_time_server(tools) -> date:
    tool = next(t for t in tools if t.name == "get_current_time")
    result = json.loads(_text(await tool.ainvoke({"timezone": TIMEZONE})))
    return date.fromisoformat(result["datetime"][:10])


def show(payload: dict) -> None:
    item, sources = payload["item"], payload["sources"]
    print(f"\n--- Draft {item['item_id']} for {item['household_id']} (pending officer approval) ---")
    for i, rec in enumerate(item["payload"]["recommendations"], 1):
        print(f"{i}. {rec['text']}")
        print(f"   cites: {', '.join(rec['citations'])}")
    print("Sources:")
    for rid, rec in sources.items():
        fields = {k: v for k, v in (rec or {}).items() if k not in ("record_id", "source")}
        print(f"  {rid}: {json.dumps(fields, ensure_ascii=False)}")
        if rec:
            print(f"      source: {rec['source']}")
    if payload.get("error"):
        print(f"! {payload['error']}")


def ask_officer(payload: dict, officer: str) -> dict:
    show(payload)
    while True:
        choice = input("Officer decision [a]pprove / [e]dit / [r]eject: ").strip().lower()[:1]
        if choice in ("a", "e", "r"):
            break
    if choice == "a":
        return {"decision": "approve", "officer": officer}
    if choice == "r":
        return {"decision": "reject", "officer": officer, "note": input("Reason (optional): ").strip()}
    edits = []
    for i, rec in enumerate(payload["item"]["payload"]["recommendations"], 1):
        new = input(f"Line {i} (Enter keeps it, '-' removes it): ").strip()
        edits.append(rec["text"] if not new else "" if new == "-" else new)
    return {"decision": "edit", "officer": officer, "edits": edits}


async def run_household(app, household_id: str, answer, transcript: Transcript | None = None) -> dict:
    # A new thread per run: nothing from an earlier run of this household carries over.
    config = {"configurable": {"thread_id": f"{household_id}:{uuid.uuid4()}"}}
    started, gate = time.monotonic(), []
    result = await app.ainvoke({"household_id": household_id}, config)
    while result.get("__interrupt__"):
        pause = result["__interrupt__"][0].value
        reply = answer(pause)
        gate.append({"shown": pause, "answer": reply})
        result = await app.ainvoke(Command(resume=reply), config)
    if transcript:
        transcript.household(household_id, state=result, gate=gate, seconds=time.monotonic() - started)
    return result["outcome"]


async def demo(args) -> int:
    officer = (args.officer or "").strip()
    if args.decide != "ask" and not officer:
        print("--decide approve/reject needs --officer: a scripted decision is still logged under a name.",
              file=sys.stderr)
        return 2
    try:
        model = make_model(args.model)
    except ModelUnavailable as e:
        print(f"Can't start the model ({model_spec(args.model)}): {e}", file=sys.stderr)
        return 2
    while not officer:
        officer = input("Officer name (recorded with every decision): ").strip()
    store, queue, audit = DataStore.load(), ReviewQueue(), AuditLog()
    tools = await MultiServerMCPClient(mcp_connections(queue)).get_tools()
    app = build_graph(model, tools, store, queue, audit, checkpointer=InMemorySaver())
    today = await today_from_time_server(tools)
    if args.decide == "ask":
        def answer(payload):
            return {**ask_officer(payload, officer), "answered_via": "prompt"}
    else:
        def answer(payload):
            show(payload)
            print(f"Decision: {args.decide} (fixed by --decide; logged as answered_via=flag, not a review)")
            return {"decision": args.decide, "officer": officer, "answered_via": "flag"}

    households = backlog(store, args.district, args.limit, today)
    transcript = Transcript(args.runs_dir, model=model_spec(args.model), model_details=model_details(args.model),
                            district=args.district, today=today.isoformat(), backlog=households,
                            decide=args.decide, officer=officer)
    print(f"Model: {model_spec(args.model)}. District: {args.district}. Today: {today} (time server). "
          f"Backlog: {', '.join(households)}")
    print("Nothing is sent to any household. Approved drafts wait in the review queue.")
    outcomes = {}
    for hh in households:
        print(f"\n=== {hh} ===")
        outcomes[hh] = await run_household(app, hh, answer, transcript)
        o = outcomes[hh]
        print(f"Outcome: {o['status']}" + (f" ({o.get('reason')})" if o.get("reason") else "")
              + (f", {o['item_id']} by {o['officer']}" if o.get("item_id") else "") + ". Not sent.")
    print(f"\nReview queue: {queue.path}\nAudit log: {audit.path}\nTranscript: {transcript.dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extension-desk")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo", help="run one backlog pass and stop at the officer approval gate")
    d.add_argument("--district", default="chiredzi", choices=sorted(DISTRICTS))
    d.add_argument("--limit", type=int, default=3, help="households in this pass (default 3; 0 = the whole district)")
    d.add_argument("--model", help="ollama:<model> or anthropic:<model> (default from EXTENSION_DESK_MODEL)")
    d.add_argument("--officer", help="officer name recorded in the audit log")
    d.add_argument("--runs-dir", help="where run transcripts go (default state/runs)")
    d.add_argument("--decide", choices=["ask", "approve", "reject"], default="ask",
                   help="answer the gate interactively (default) or with a fixed decision")
    e = sub.add_parser("eval", help="run the eval tasks against the configured model")
    e.add_argument("--task", action="append", help="run only this task (repeatable)")
    e.add_argument("--model", help="ollama:<model> or anthropic:<model> (default from EXTENSION_DESK_MODEL)")
    e.add_argument("--results-dir", help="where results go (default evals/results)")
    e.add_argument("--update-evals", action="store_true", help="rewrite the results table in EVALS.md")
    args = parser.parse_args(argv)
    if args.command == "eval":
        from extension_desk.evals.harness import run_all
        try:
            return asyncio.run(run_all(args))
        except ModelUnavailable as err:
            print(f"Can't start the model ({model_spec(args.model)}): {err}", file=sys.stderr)
            return 2
    return asyncio.run(demo(args))


if __name__ == "__main__":
    raise SystemExit(main())
