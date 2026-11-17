"""`uv run extension-desk demo`: one backlog pass over a pilot district.

For each household in the backlog the agent gathers evidence, drafts, and
stops at the officer approval gate. You answer as the officer. Nothing is
sent to any household; approved drafts wait in the review queue.
"""

import argparse
import asyncio
import inspect
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
from extension_desk.agent.replay import (ReplayDiverged, ReplayModel, gate_difference, load_recording,
                                         token_usage, whole_run_difference)
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


def _short(value, limit: int = 110) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def trace_update(node: str, update: dict) -> list[str]:
    """Lines describing one graph step, as it happens: what the model called,
    what came back, and what the checks decided. Long arguments are cut and
    results show record IDs only; the full content is in the transcript."""
    lines = []
    for msg in (update or {}).get("messages", []):
        kind = getattr(msg, "type", "")
        if kind == "ai":
            calls = getattr(msg, "tool_calls", [])
            if _text(msg).strip():
                label = "model (text)" if calls else "model (text, no tool call)"
                lines.append(f"  {label}: {_short(_text(msg))}")
            for tc in calls:
                lines.append(f"  model -> {tc['name']}({_short(tc['args'], 90)})")
        elif kind == "tool":
            text = _text(msg)
            if getattr(msg, "status", "") == "error":
                # Graph-made replies have no tool name: say which step made them.
                label = msg.name or {"check": "citation check", "nudge": "nudge"}.get(node, node)
                lines.append(f"  {label} !! {_short(text)}")
            else:
                try:
                    cites = json.loads(text).get("citations", [])
                    lines.append(f"  {msg.name} <- {len(cites)} record(s)"
                                 + (f": {', '.join(cites[:4])}{' ...' if len(cites) > 4 else ''}" if cites else ""))
                except (json.JSONDecodeError, AttributeError):
                    lines.append(f"  {msg.name or node} <- {_short(text)}")
        elif kind == "human" and node in ("queue_draft", "nudge"):
            lines.append(f"  {node} !! {_short(msg.content)}")
    if node == "clock" and update.get("today"):
        lines.append(f"  time server -> today is {update['today']}")
    if node == "queue_draft" and update.get("item"):
        item = update["item"]
        lines.append(f"  draft_advisory -> queued {item['item_id']} as {item.get('status')} (nothing sent)")
    return lines


def token_line(usage: dict, replayed: bool = False) -> str:
    if not usage["model_calls"]:
        return "Tokens: not reported by this model."
    return (f"Tokens: {usage['model_calls']} model calls, {usage['input_tokens']:,} in + "
            f"{usage['output_tokens']:,} out = {usage['input_tokens'] + usage['output_tokens']:,}"
            + (" (as logged when the replayed run was recorded)" if replayed else "") + ".")


async def run_household(app, household_id: str, answer, transcript: Transcript | None = None,
                        trace: bool = False, tokens: bool = False, replayed: bool = False,
                        verify=None, on_update=None) -> dict:
    """`verify(messages, shown)` returns a problem or None. It runs before
    the officer is asked anything (with what the officer would be shown) and
    when the run ends (shown=None); a problem stops the run with
    ReplayDiverged. The transcript is written either way.
    `on_update(node, update)` sees every graph step (the officer page uses it);
    `answer` may be a coroutine function (the page awaits the officer's click)."""
    # A new thread per run: nothing from an earlier run of this household carries over.
    config = {"configurable": {"thread_id": f"{household_id}:{uuid.uuid4()}"}}
    started, gate = time.monotonic(), []
    step = {"household_id": household_id}
    problem = None
    while True:
        pause = None
        try:
            async for chunk in app.astream(step, config, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        pause = update[0].value
                        continue
                    if on_update:
                        on_update(node, update)
                    if trace:
                        for line in trace_update(node, update):
                            print(line, flush=True)
        except ReplayDiverged as e:  # raised by the replayed model before a turn
            problem = str(e).removeprefix("replay stopped: ")
            break
        if pause is None:
            break
        problem = verify and verify((await app.aget_state(config)).values.get("messages", []), pause)
        if problem:
            break
        if trace:
            print("  officer gate: paused for the officer", flush=True)
        reply = answer(pause)
        if inspect.isawaitable(reply):
            reply = await reply
        gate.append({"shown": pause, "answer": reply})
        step = Command(resume=reply)
    result = dict((await app.aget_state(config)).values)
    problem = problem or (verify and verify(result.get("messages", []), None))
    if problem:
        result["outcome"] = {"status": "replay_diverged", "reason": problem, "delivered": False}
    if transcript:
        transcript.household(household_id, state=result, gate=gate, seconds=time.monotonic() - started)
    if problem:
        raise ReplayDiverged(f"replay stopped: {problem}")
    if tokens:
        print(token_line(token_usage(result.get("messages", [])), replayed), flush=True)
    return result["outcome"]


async def demo(args) -> int:
    officer = (args.officer or "").strip()
    if args.decide != "ask" and not officer:
        print("--decide approve/reject needs --officer: a scripted decision is still logged under a name.",
              file=sys.stderr)
        return 2
    store = DataStore.load()
    in_district = {hh["record_id"] for hh in store.households(args.district)}
    unknown = [hh for hh in (args.household or []) if hh not in in_district]
    if unknown:
        print(f"Not a household in {args.district}: {', '.join(unknown)}", file=sys.stderr)
        return 2
    recording = None
    if args.replay:
        if args.model:
            print("--replay replays a recorded model; it can't be combined with --model.", file=sys.stderr)
            return 2
        try:
            recording = load_recording(args.replay)
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"Can't load the recording: {e!r}", file=sys.stderr)
            return 2
        if recording["household_id"] not in in_district:
            print(f"The recording is for {recording['household_id']}, not a household in {args.district}.",
                  file=sys.stderr)
            return 2
        if args.household and set(args.household) != {recording["household_id"]}:
            print(f"The recording is for {recording['household_id']} only.", file=sys.stderr)
            return 2
        model = ReplayModel(recorded=recording["messages"], delay=args.replay_delay)
        spec = f"replay of {recording['model'] or 'a recorded model'}"
    else:
        try:
            model = make_model(args.model)
        except ModelUnavailable as e:
            print(f"Can't start the model ({model_spec(args.model)}): {e}", file=sys.stderr)
            return 2
        spec = model_spec(args.model)
    while not officer:
        officer = input("Officer name (recorded with every decision): ").strip()
    queue, audit = ReviewQueue(), AuditLog()
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

    if recording:
        households = [recording["household_id"]]
    elif args.household:
        households = list(dict.fromkeys(args.household))
    else:
        households = backlog(store, args.district, args.limit, today)
    meta = ({"model": f"replay:{recording['model']}",
             "replayed_from": f"{recording['path'].parent.name}/{recording['path'].name}"} if recording
            else {"model": spec, "model_details": model_details(args.model)})
    transcript = Transcript(args.runs_dir, **meta, district=args.district, today=today.isoformat(),
                            backlog=households, decide=args.decide, officer=officer)
    print(f"Model: {spec}. District: {args.district}. Today: {today} (time server). "
          f"Households: {', '.join(households)}")
    if recording:
        print(f"REPLAY: the model's turns are replayed from {recording['path'].parent.name}/"
              f"{recording['path'].name}. Our MCP server, the time server, the citation checks, the review "
              "queue and the officer gate are running live now.")
    print("Nothing is sent to any household. Approved drafts wait in the review queue.")
    outcomes = {}
    for hh in households:
        print(f"\n=== {hh}{' (model turns replayed)' if recording else ''} ===")
        try:
            def verify(msgs, shown):
                return whole_run_difference(msgs, recording["messages"]) or (
                    gate_difference(shown, recording["shown"]) if shown is not None else None)
            verify = verify if recording else None
            outcomes[hh] = await run_household(app, hh, answer, transcript, trace=not args.quiet,
                                               tokens=True, replayed=bool(recording), verify=verify)
        except ReplayDiverged as e:
            print(f"\n{e}\nThe live system no longer matches the recording, so the replay stopped here.",
                  file=sys.stderr)
            return 3
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
    d.add_argument("--quiet", action="store_true", help="don't print each tool call as it happens")
    d.add_argument("--decide", choices=["ask", "approve", "reject"], default="ask",
                   help="answer the gate interactively (default) or with a fixed decision")
    d.add_argument("--household", action="append", metavar="HH",
                   help="run this household instead of the backlog (repeatable), e.g. hh:018")
    d.add_argument("--replay", metavar="RUN/HH.json",
                   help="replay a recorded run's model turns; servers, checks and the officer gate run live")
    d.add_argument("--replay-delay", type=float, default=1.0, metavar="SECONDS",
                   help="pause before each replayed model turn (default 1.0)")
    o = sub.add_parser("officer", help="open the officer's review page in a browser")
    o.add_argument("--district", default="chiredzi", choices=sorted(DISTRICTS))
    o.add_argument("--replay", metavar="RUN/HH.json",
                   help="replay this recording's model turns for its household; others run the live model")
    o.add_argument("--replay-delay", type=float, default=2.0, metavar="SECONDS",
                   help="pause before each replayed model answer, so the page can be narrated (default 2)")
    o.add_argument("--model", help="live model, ollama:<model> or anthropic:<model>")
    o.add_argument("--runs-dir", help="where run transcripts go (default state/runs)")
    o.add_argument("--port", type=int, default=8765)
    e = sub.add_parser("eval", help="run the eval tasks against the configured model")
    e.add_argument("--task", action="append", help="run only this task (repeatable)")
    e.add_argument("--model", help="ollama:<model> or anthropic:<model> (default from EXTENSION_DESK_MODEL)")
    e.add_argument("--results-dir", help="where results go (default evals/results)")
    e.add_argument("--update-evals", action="store_true", help="rewrite the results table in EVALS.md")
    e.add_argument("--regrade", nargs="+", metavar="RUN_DIR",
                   help="re-score stored runs with today's checks (no model); writes regrade-<commit>.json")
    args = parser.parse_args(argv)
    if args.command == "eval":
        from extension_desk.evals.harness import run_all
        try:
            return asyncio.run(run_all(args))
        except ModelUnavailable as err:
            print(f"Can't start the model ({model_spec(args.model)}): {err}", file=sys.stderr)
            return 2
    if args.command == "officer":
        from extension_desk.officer.app import main as officer_main
        return officer_main(args)
    return asyncio.run(demo(args))


if __name__ == "__main__":
    raise SystemExit(main())
