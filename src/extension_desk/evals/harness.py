"""Run the eval tasks and record what actually happened.

    uv run extension-desk eval                    # all tasks, configured model
    uv run extension-desk eval --task missing_data
    uv run extension-desk eval --update-evals     # also rewrite the table in EVALS.md

Each task gets its own fixture data, queue, audit log and transcript under
evals/results/<UTC timestamp>/<task>/, plus results.json for the run. The
officer gate is answered by the harness ("reject", logged answered_via=flag),
so no eval decision can pass for a human review.
"""

import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver

from extension_desk.agent import cli
from extension_desk.agent.audit import AuditLog
from extension_desk.agent.graph import build_graph
from extension_desk.agent.models import make_model, model_details, model_spec
from extension_desk.agent.transcript import Transcript, _commit, command
from extension_desk.data import DataStore
from extension_desk.evals import fixtures
from extension_desk.evals.tasks import TASKS, Run, Task
from extension_desk.review_queue import ReviewQueue

ROOT = Path(__file__).resolve().parents[3]
RESULTS = ROOT / "evals" / "results"
EVALS_MD = ROOT / "EVALS.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
OFFICER = "eval harness (no human review)"


def _scripted(script):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class Scripted(BaseChatModel):
        steps: list
        calls: int = 0

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            step = self.steps[min(self.calls, len(self.steps) - 1)]
            self.calls += 1
            calls = [{**tc, "id": f"{tc['id']}-{self.calls}"} for tc in step.tool_calls]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(step.content, tool_calls=calls))])

    return Scripted(steps=script)


async def run_task(task: Task, model, out: Path) -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"eval-{task.name}-"))
    data_dir = fixtures.make(work / "data", task.fixture)
    store, queue, audit = DataStore.load(data_dir), ReviewQueue(work / "queue.sqlite3"), AuditLog(work / "audit.jsonl")
    tools = await MultiServerMCPClient(cli.mcp_connections(queue, data_dir)).get_tools()
    app = build_graph(model if task.model == "real" else _scripted(task.script), tools, store, queue, audit,
                      checkpointer=InMemorySaver())
    fixture = json.loads((data_dir / "fixture.json").read_text(encoding="utf-8"))
    transcript = Transcript(out, name=task.name, task=task.name, covers=task.covers, fixture=fixture,
                            model=task.model)
    started = time.monotonic()
    error = None
    try:
        await cli.run_household(app, task.household_id,
                                lambda pause: {"decision": "reject", "officer": OFFICER, "answered_via": "flag"},
                                transcript)
    except Exception as e:  # a crash is a failed task, recorded, not a crashed harness
        error = f"{type(e).__name__}: {e}"
    seconds = round(time.monotonic() - started, 1)
    record_file = transcript.dir / f"{task.household_id.replace(':', '_')}.json"
    record = json.loads(record_file.read_text(encoding="utf-8")) if record_file.exists() else {}
    from langchain_core.messages import messages_from_dict
    state = {**record, "messages": messages_from_dict(record.get("messages", []))}
    run = Run(task.household_id, record.get("outcome", {}), state, queue.items(), audit.entries(), store)
    passed, detail = (False, f"crashed: {error}") if error else task.check(run)
    (transcript.dir / "queue.json").write_text(json.dumps(queue.items(), indent=2), encoding="utf-8")
    (transcript.dir / "audit.jsonl").write_text("".join(json.dumps(e) + "\n" for e in audit.entries()),
                                                encoding="utf-8")
    return {"task": task.name, "covers": task.covers, "household_id": task.household_id, "model": task.model,
            "fixture": bool(task.fixture), "expected": task.expected, "passed": passed, "detail": detail,
            "outcome": run.outcome.get("status") or ("crash" if error else None), "seconds": seconds}


def table(results: dict) -> str:
    meta = results["meta"]
    lines = [START,
             f"Run `{meta['run']}` on {meta['started_at'][:10]}, model `{meta['model']}` "
             f"({meta['model_details'].get('parameter_size', '')} {meta['model_details'].get('quantization_level', '')}, "
             f"digest `{(meta['model_details'].get('digest') or '')[:12]}`), commit `{(meta['commit'] or '')[:12]}`. "
             f"**{sum(r['passed'] for r in results['tasks'])} of {len(results['tasks'])} passed.** "
             f"Transcripts: `evals/results/{meta['run']}/`.",
             "",
             "| # | Task | Covers | Expected | Result | Outcome | Notes |",
             "|---|------|--------|----------|--------|---------|-------|"]
    for i, r in enumerate(results["tasks"], 1):
        notes = r["detail"].replace("|", "\\|").replace("\n", " ")
        tags = ", ".join(t for t, on in (("scripted model", r["model"] == "scripted"), ("fixture", r["fixture"])) if on)
        lines.append(f"| {i} | `{r['task']}` | {r['covers']}{f' ({tags})' if tags else ''} | {r['expected']} | "
                     f"{'PASS' if r['passed'] else '**FAIL**'} | {r['outcome']} | {notes} |")
    lines.append(END)
    return "\n".join(lines)


def update_evals(results: dict) -> None:
    text = EVALS_MD.read_text(encoding="utf-8")
    block = table(results)
    if START in text and END in text:
        text = text[:text.index(START)] + block + text[text.index(END) + len(END):]
    else:
        raise RuntimeError(f"EVALS.md needs {START} and {END} markers around the results table")
    EVALS_MD.write_text(text, encoding="utf-8")


async def run_all(args) -> int:
    chosen = [t for t in TASKS if not args.task or t.name in args.task]
    if not chosen:
        print(f"no such task; choose from {', '.join(t.name for t in TASKS)}", file=sys.stderr)
        return 2
    model = make_model(args.model) if any(t.model == "real" for t in chosen) else None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.results_dir or RESULTS) / stamp
    out.mkdir(parents=True)
    meta = {"run": stamp, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model_spec(args.model) if model else "none (scripted tasks only)",
            "model_details": model_details(args.model) if model else {}, "commit": _commit(),
            "command": command()}
    results = {"meta": meta, "tasks": []}
    for task in chosen:
        print(f"{task.name} ...", flush=True)
        r = await run_task(task, model, out)
        results["tasks"].append(r)
        print(f"  {'PASS' if r['passed'] else 'FAIL'} ({r['seconds']}s, {r['outcome']}): {r['detail']}", flush=True)
        (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    meta["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n{sum(r['passed'] for r in results['tasks'])}/{len(results['tasks'])} passed. Results: {out}")
    if args.update_evals:
        if len(chosen) != len(TASKS) or not model:
            print("Not updating EVALS.md: only a full run on a real model replaces the table.", file=sys.stderr)
            return 2
        update_evals(results)
        print(f"Updated {EVALS_MD}")
    return 0
