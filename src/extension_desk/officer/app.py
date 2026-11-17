"""`extension-desk officer`: the review queue as an officer would use it.

A small local web page over the same agent, MCP servers, checks, review
queue and audit log as `extension-desk demo`. The officer picks a household,
watches the agent gather evidence in plain English, then approves, edits or
rejects the draft under their own name. Nothing is sent to any household.

With --replay, runs of the recorded household replay the model's answers
(see agent/replay.py; the page says so on screen); every other household
runs the live model.
"""

import asyncio
import contextlib
import sys
import uuid
from datetime import date
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from extension_desk.agent.audit import AuditLog
from extension_desk.agent.cli import mcp_connections, run_household, today_from_time_server
from extension_desk.agent.graph import build_graph
from extension_desk.agent.models import ModelUnavailable, make_model, model_details, model_spec
from extension_desk.agent.replay import (ReplayDiverged, ReplayModel, gate_difference, load_recording,
                                         whole_run_difference)
from extension_desk.agent.transcript import Transcript
from extension_desk.data import DataStore
from extension_desk.officer.steps import describe
from extension_desk.review_queue import ReviewQueue

PAGE = Path(__file__).with_name("page.html")


class Run:
    def __init__(self, household: str, replayed: bool):
        self.id = uuid.uuid4().hex[:8]
        self.household, self.replayed = household, replayed
        self.steps: list[dict] = []
        self.state = "running"          # running | gate | done | diverged | error
        self.gate: dict | None = None
        self.outcome: dict | None = None
        self.error = ""
        self.tokens = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.future: asyncio.Future | None = None

    def view(self) -> dict:
        return {"id": self.id, "household": self.household, "replayed": self.replayed, "state": self.state,
                "steps": self.steps, "gate": self.gate, "outcome": self.outcome, "error": self.error,
                "tokens": self.tokens}

    def on_update(self, node: str, update: dict) -> None:
        self.steps.extend(describe(node, update))
        for m in (update or {}).get("messages", []):
            usage = getattr(m, "usage_metadata", None) if getattr(m, "type", "") == "ai" else None
            if usage:
                self.tokens["model_calls"] += 1
                self.tokens["input_tokens"] += usage.get("input_tokens", 0)
                self.tokens["output_tokens"] += usage.get("output_tokens", 0)


class Desk:
    """Everything the page talks to. One run at a time: one officer, one laptop."""

    def __init__(self, district: str, recording: dict | None, model: str | None, delay: float,
                 runs_dir=None):
        self.district, self.recording, self.model, self.delay = district, recording, model, delay
        self.runs_dir = runs_dir
        self.store, self.queue, self.audit = DataStore.load(), ReviewQueue(), AuditLog()
        self.tools, self.today = None, None
        self.runs: dict[str, Run] = {}
        self.current: Run | None = None

    async def start(self) -> None:
        self.tools = await MultiServerMCPClient(mcp_connections(self.queue)).get_tools()
        self.today = await today_from_time_server(self.tools)

    def households(self) -> list[dict]:
        today = self.today or date.today()
        out = []
        for hh in self.store.households(self.district):
            plots = self.store.plots(hh["record_id"])
            out.append({"id": hh["record_id"], "members": hh.get("members"),
                        "days": (today - date.fromisoformat(hh["last_officer_visit"])).days,
                        "crops": list(dict.fromkeys(p["crop"] for p in plots)),
                        "replay": bool(self.recording and self.recording["household_id"] == hh["record_id"])})
        return sorted(out, key=lambda h: -h["days"])

    def begin(self, household: str) -> Run:
        if self.current and self.current.state in ("running", "gate"):
            raise RuntimeError(f"{self.current.household} is still open; finish it first")
        if household not in {h["record_id"] for h in self.store.households(self.district)}:
            raise ValueError(f"not a household in {self.district}: {household}")
        replayed = bool(self.recording and self.recording["household_id"] == household)
        run = Run(household, replayed)
        self.runs[run.id], self.current = run, run
        asyncio.get_running_loop().create_task(self._go(run))
        return run

    async def _go(self, run: Run) -> None:
        try:
            if run.replayed:
                model = ReplayModel(recorded=self.recording["messages"], delay=self.delay)
                meta = {"model": f"replay:{self.recording['model']}",
                        "replayed_from": f"{self.recording['path'].parent.name}/{self.recording['path'].name}"}
            else:
                model = make_model(self.model)
                meta = {"model": model_spec(self.model), "model_details": model_details(self.model)}
            app = build_graph(model, self.tools, self.store, self.queue, self.audit, checkpointer=InMemorySaver())
            transcript = Transcript(self.runs_dir, **meta, district=self.district, today=self.today.isoformat(),
                                    backlog=[run.household], decide="officer page", officer=None)

            async def answer(pause):
                run.gate, run.state = pause, "gate"
                run.future = asyncio.get_running_loop().create_future()
                reply = await run.future
                run.state = "running"
                return reply

            verify = None
            if run.replayed:
                def verify(msgs, shown):
                    return whole_run_difference(msgs, self.recording["messages"]) or (
                        gate_difference(shown, self.recording["shown"]) if shown is not None else None)
            run.outcome = await run_household(app, run.household, answer, transcript, verify=verify,
                                              on_update=run.on_update)
            run.state = "done"
        except ReplayDiverged as e:
            run.state, run.error = "diverged", str(e)
        except ModelUnavailable as e:
            run.state, run.error = "error", f"The live model isn't available: {e}"
        except Exception as e:  # shown on the page rather than lost in a background task
            run.state, run.error = "error", f"{type(e).__name__}: {e}"

    def decide(self, run_id: str, body: dict) -> Run:
        run = self.runs.get(run_id)
        if not run or run.state != "gate" or not run.future or run.future.done():
            raise RuntimeError("this draft isn't waiting for a decision")
        decision = body.get("decision")
        officer = str(body.get("officer") or "").strip()
        if decision not in ("approve", "edit", "reject"):
            raise ValueError("decision must be approve, edit or reject")
        if not officer:
            raise ValueError("enter your name: every decision is recorded under it")
        reply = {"decision": decision, "officer": officer, "answered_via": "prompt"}  # read on the page
        if decision == "edit":
            reply["edits"] = [str(e) for e in body.get("edits") or []]
        if decision == "reject":
            reply["note"] = str(body.get("note") or "")
        run.future.set_result(reply)
        return run


def same_origin(request: Request) -> bool:
    """A run or decision must come from this page: a JSON request whose
    Origin, if the browser sends one, is this server. Stops another site open
    in the same browser, or a plain form post, from starting or signing."""
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return False
    origin = request.headers.get("origin")
    return origin is None or origin.split("://", 1)[-1] == request.headers.get("host")


def create_app(desk: Desk) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await desk.start()
        yield

    async def page(_: Request):
        return HTMLResponse(PAGE.read_text(encoding="utf-8"))

    async def info(_: Request):
        return JSONResponse({"district": desk.district, "today": desk.today and desk.today.isoformat(),
                             "replay": bool(desk.recording), "households": desk.households(),
                             "current": desk.current and desk.current.view()})

    async def begin(request: Request):
        if not same_origin(request):
            return JSONResponse({"error": "requests must come from this page"}, status_code=403)
        body = await request.json()
        try:
            run = desk.begin(str(body.get("household", "")))
        except (RuntimeError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse(run.view())

    async def status(request: Request):
        run = desk.runs.get(request.path_params["run_id"])
        return JSONResponse(run.view()) if run else JSONResponse({"error": "no such run"}, status_code=404)

    async def decide(request: Request):
        if not same_origin(request):
            return JSONResponse({"error": "requests must come from this page"}, status_code=403)
        try:
            run = desk.decide(request.path_params["run_id"], await request.json())
        except (RuntimeError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        await asyncio.sleep(0.05)
        return JSONResponse(run.view())

    return Starlette(routes=[
        Route("/", page), Route("/api/info", info),
        Route("/api/run", begin, methods=["POST"]),
        Route("/api/run/{run_id}", status),
        Route("/api/run/{run_id}/decide", decide, methods=["POST"]),
    ], lifespan=lifespan)


def main(args) -> int:
    import uvicorn
    recording = None
    if args.replay:
        try:
            recording = load_recording(args.replay)
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"Can't load the recording: {e!r}", file=sys.stderr)
            return 2
    desk = Desk(args.district, recording, args.model, args.replay_delay, args.runs_dir)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Officer review page: {url}  (Ctrl+C to stop). Nothing is sent to any household.")
    if recording:
        print(f"{recording['household_id']} replays the model's answers from {recording['path'].parent.name}; "
              "every other household runs the live model.")
    # Loopback only: whoever uses this page signs decisions, so other machines must not reach it.
    uvicorn.run(create_app(desk), host="127.0.0.1", port=args.port, log_level="warning")
    return 0
