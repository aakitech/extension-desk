"""The agent graph against both real MCP servers (ours and the reference
`time` server, over stdio), driven by a scripted model so runs are exact.

The invariant: nothing leaves the queue's `pending_approval` state without
an officer decision, and every decision has its audit entry (inputs,
outputs, timestamp, officer). Drafts that fail the citation check never
reach the queue.
"""

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import json
import sqlite3

from langchain_core.tools import StructuredTool

from extension_desk.agent import cli, gate
from extension_desk.agent.audit import AuditLog
from extension_desk.agent.cli import backlog, mcp_connections
from extension_desk.agent.graph import MAX_TURNS, build_graph, citation_problems
from extension_desk.data import DataStore
from extension_desk.review_queue import PENDING, ReviewQueue

HH = "hh:001"  # chiredzi; plot:001-1 (sorghum)
RAIN = "rain:chiredzi:2026-09-d3"
PEST = "pest:chiredzi:001"


class ScriptedModel(BaseChatModel):
    """Returns the scripted AI messages in order; repeats the last one."""
    script: list
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        # A fresh message each time, like a real model: same IDs would make
        # add_messages replace the earlier turn instead of appending.
        calls = [{**tc, "id": f"{tc['id']}-{self.calls}"} for tc in step.tool_calls]
        msg = AIMessage(step.content, tool_calls=calls)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def call(name, args, n=[0]):
    n[0] += 1
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"c{n[0]}", "type": "tool_call"}])


def submit(*recs):
    return call("submit_draft", {"recommendations": [{"text": t, "citations": list(c)} for t, c in recs]})


GATHER = [call("get_plot_records", {"household_id": HH}),
          call("get_rainfall", {"district": "chiredzi"}),
          call("get_pest_alerts", {"district": "chiredzi"})]
GOOD = submit(("Wait for 20 mm of rain in a dekad before dry planting.", [RAIN, "plot:001-1"]),
              ("Scout the sorghum for fall armyworm twice a week.", [PEST]))


@pytest.fixture(scope="module")
def tools():
    return asyncio.run(MultiServerMCPClient(mcp_connections(_SHARED_QUEUE)).get_tools())


_SHARED_QUEUE = None


@pytest.fixture(scope="module", autouse=True)
def shared_queue(tmp_path_factory):
    # The MCP server is told its queue path when its connection is made, so the
    # module's tools and every test's ReviewQueue point at the same file.
    global _SHARED_QUEUE
    _SHARED_QUEUE = ReviewQueue(tmp_path_factory.mktemp("q") / "queue.sqlite3")
    yield _SHARED_QUEUE


@pytest.fixture
def env(tools, tmp_path):
    store, queue, audit = DataStore.load(), _SHARED_QUEUE, AuditLog(tmp_path / "audit.jsonl")
    before = {i["item_id"] for i in queue.items()}

    def new_items():
        return [i for i in queue.items() if i["item_id"] not in before]

    def app(*script):
        model = ScriptedModel(script=list(script))
        return build_graph(model, tools, store, queue, audit, checkpointer=InMemorySaver()), model

    return app, queue, audit, new_items


def run(app, answers=()):
    """Run one household; answer each gate pause with the next answer."""
    answers, pauses = list(answers), []

    async def go():
        config = {"configurable": {"thread_id": "t"}}
        result = await app.ainvoke({"household_id": HH}, config)
        while result.get("__interrupt__"):
            pauses.append(result["__interrupt__"][0].value)
            result = await app.ainvoke(Command(resume=answers.pop(0)), config)
        return result

    return asyncio.run(go()), pauses


# --- the full path ----------------------------------------------------------

def test_approved_draft_is_logged_with_inputs_outputs_time_and_officer(env):
    app, queue, audit, new_items = env
    graph, _ = app(*GATHER, GOOD)
    result, pauses = run(graph, [{"decision": "approve", "officer": "T. Moyo"}])

    # The officer saw the draft and every cited record before deciding.
    assert len(pauses) == 1 and pauses[0]["item"]["status"] == PENDING
    assert set(pauses[0]["sources"]) == {RAIN, "plot:001-1", PEST}
    assert all(pauses[0]["sources"].values())

    outcome = result["outcome"]
    assert outcome["status"] == "approved" and outcome["delivered"] is False
    [item] = new_items()
    assert item["status"] == "approved" and item["decided_by"] == "T. Moyo" and item["decided_at"]
    [entry] = audit.entries()
    assert entry["event"] == "officer_decision" and entry["officer"] == "T. Moyo"
    assert entry["item_id"] == item["item_id"] and entry["timestamp"]
    assert entry["inputs"]["payload"] == item["payload"] and entry["inputs"]["citations"] == item["citations"]
    assert entry["outputs"] == {"final_payload": item["payload"], "delivered": False}


def test_real_time_server_gives_harare_date(env):
    # The real server's date matches Harare. That the graph *uses* the server
    # (rather than its own clock) is test_clock_uses_the_time_server_answer.
    app, *_ = env
    graph, _ = app(*GATHER, GOOD)
    result, _ = run(graph, [{"decision": "reject", "officer": "T. Moyo"}])
    harare_today = datetime.now(ZoneInfo("Africa/Harare")).date().isoformat()
    assert result["today"] == harare_today
    assert any("from the time server" in m.content for m in result["messages"] if isinstance(m, HumanMessage))


def test_rejected_draft_is_logged_and_nothing_is_sent(env):
    app, queue, audit, new_items = env
    graph, _ = app(*GATHER, GOOD)
    result, _ = run(graph, [{"decision": "reject", "officer": "T. Moyo", "note": "rain too uncertain"}])
    assert result["outcome"]["status"] == "rejected" and result["outcome"]["delivered"] is False
    [item] = new_items()
    assert item["status"] == "rejected"
    [entry] = audit.entries()
    assert entry["decision"] == "reject" and entry["note"] == "rain too uncertain"
    assert entry["outputs"]["delivered"] is False


def test_officer_edit_keeps_citations_and_logs_both_versions(env):
    app, queue, audit, new_items = env
    graph, _ = app(*GATHER, GOOD)
    result, pauses = run(graph, [
        {"decision": "edit", "officer": "T. Moyo", "edits": ["only one line"]},  # wrong count
        {"decision": "edit", "officer": "", "edits": ["Wait for 25 mm.", ""]},     # no name
        {"decision": "edit", "officer": "T. Moyo", "edits": ["Wait for 25 mm before planting.", ""]},
    ])
    # Bad answers re-ask with the reason and record nothing.
    assert len(pauses) == 3 and "one text per recommendation" in pauses[1]["error"]
    assert "officer's name" in pauses[2]["error"]
    [item] = new_items()
    assert item["status"] == "approved"
    assert item["final_payload"]["recommendations"] == [
        {"text": "Wait for 25 mm before planting.", "citations": [RAIN, "plot:001-1"]}]
    [entry] = audit.entries()
    assert entry["decision"] == "edit"
    assert len(entry["inputs"]["payload"]["recommendations"]) == 2
    assert entry["outputs"]["final_payload"] == item["final_payload"]


# --- the citation check -----------------------------------------------------

def test_real_record_never_returned_this_run_is_rejected_then_dropped(env):
    # rain:chiredzi:2023-07-d1 exists, but no tool returned it in this run.
    app, queue, audit, new_items = env
    stale = submit(("Plant now, the rains have come.", ["rain:chiredzi:2023-07-d1"]))
    graph, model = app(*GATHER, stale, stale)
    result, pauses = run(graph)
    assert pauses == [] and result["outcome"]["status"] == "dropped"
    assert "no tool returned in this run: rain:chiredzi:2023-07-d1" in result["outcome"]["reason"]
    assert new_items() == []
    [entry] = audit.entries()
    assert entry["event"] == "draft_dropped" and entry["inputs"]["attempts"] == 2


def test_failed_check_gets_one_retry_with_reasons(env):
    app, queue, audit, new_items = env
    bad = submit(("Plant now.", ["hh:001"]))  # household record only: who, not why
    graph, model = app(*GATHER, bad, GOOD)
    result, pauses = run(graph, [{"decision": "approve", "officer": "T. Moyo"}])
    assert result["outcome"]["status"] == "approved" and len(new_items()) == 1
    feedback = [m for m in result["messages"] if getattr(m, "status", "") == "error"]
    assert "needs a rain:, price:, pest: or plot: citation" in feedback[0].content


def test_server_check_is_a_second_line_of_defence(env):
    # A district plot lookup returns other households' plots, so plot:002-1 is
    # "seen" this run; the MCP server still refuses it for hh:001.
    app, queue, audit, new_items = env
    wrong = submit(("Rotate with groundnuts.", ["plot:002-1"]))
    graph, _ = app(call("get_plot_records", {"district": "chiredzi"}), wrong, wrong)
    result, pauses = run(graph)
    assert pauses == [] and result["outcome"]["status"] == "dropped"
    assert "not about hh:001" in result["outcome"]["reason"]
    assert new_items() == []


def test_no_advice_is_logged_and_queues_nothing(env):
    app, queue, audit, new_items = env
    graph, _ = app(*GATHER, call("no_advice", {"reason": "September rain is preliminary data only."}))
    result, pauses = run(graph)
    assert pauses == [] and result["outcome"]["status"] == "no_advice"
    assert new_items() == []
    [entry] = audit.entries()
    assert entry["event"] == "no_advice" and RAIN in entry["inputs"]["seen"]


def test_model_that_never_submits_is_dropped_after_max_turns(env):
    app, queue, audit, new_items = env
    graph, model = app(AIMessage("I think they should plant maize."))
    result, pauses = run(graph)
    assert result["outcome"]["status"] == "dropped" and model.calls == MAX_TURNS
    assert new_items() == []


def test_mixing_submit_with_other_calls_is_not_accepted(env):
    app, queue, audit, new_items = env
    mixed = AIMessage("", tool_calls=[
        {"name": "get_rainfall", "args": {"district": "chiredzi"}, "id": "m1", "type": "tool_call"},
        {"name": "submit_draft", "args": {"recommendations": []}, "id": "m2", "type": "tool_call"}])
    graph, _ = app(mixed, *GATHER, GOOD)
    result, pauses = run(graph, [{"decision": "reject", "officer": "T. Moyo"}])
    assert len(pauses) == 1  # the mixed turn was bounced, the clean one went through
    assert any("Not run" in str(m.content) for m in result["messages"])


@pytest.mark.parametrize("recs, problem", [
    ([], "no recommendations"),
    ([{"text": "x", "citations": []}], "no citations"),
    ([{"text": " ", "citations": [RAIN]}], "no text"),
    ([{"text": "x", "citations": ["rain:chiredzi:2099-01-d1"]}], "no tool returned"),
])
def test_citation_problems(recs, problem):
    assert any(problem in p for p in citation_problems(recs, [RAIN]))


# --- the gate on its own ----------------------------------------------------

def _pending(queue):
    return queue.add("advisory", HH, {"recommendations": [{"text": "x", "citations": [RAIN]}]},
                     [RAIN], "test")


def test_a_decision_happens_once(tmp_path):
    queue, audit = ReviewQueue(tmp_path / "q.sqlite3"), AuditLog(tmp_path / "a.jsonl")
    item = _pending(queue)
    gate.decide(queue, audit, item["item_id"], "approve", "T. Moyo")
    with pytest.raises(ValueError, match="not pending"):
        gate.decide(queue, audit, item["item_id"], "reject", "Someone Else")
    assert queue.get(item["item_id"])["status"] == "approved" and len(audit.entries()) == 1


def test_no_decision_without_its_audit_entry(tmp_path):
    queue = ReviewQueue(tmp_path / "q.sqlite3")
    item = _pending(queue)

    class BrokenAudit(AuditLog):
        def append(self, event, **fields):
            raise OSError("disk full")

    with pytest.raises(OSError):
        gate.decide(queue, BrokenAudit(tmp_path / "a.jsonl"), item["item_id"], "approve", "T. Moyo")
    assert queue.get(item["item_id"])["status"] == PENDING  # rolled back


@pytest.mark.parametrize("decision, officer, edits, message", [
    ("send", "T. Moyo", None, "decision must be"),
    ("approve", " ", None, "officer's name"),
    ("approve", "T. Moyo", ["y"], "only accepted with decision='edit'"),
    ("edit", "T. Moyo", [""], "reject the draft instead"),
])
def test_gate_rejects_bad_answers_and_records_nothing(tmp_path, decision, officer, edits, message):
    queue, audit = ReviewQueue(tmp_path / "q.sqlite3"), AuditLog(tmp_path / "a.jsonl")
    item = _pending(queue)
    with pytest.raises(ValueError, match=message):
        gate.decide(queue, audit, item["item_id"], decision, officer, edits)
    assert queue.get(item["item_id"])["status"] == PENDING and audit.entries() == []


def test_backlog_puts_pest_exposed_planted_households_first():
    store = DataStore.load()
    order = backlog(store, "chiredzi", 25, today=date(2026, 10, 12))
    pest_crops = {c for a in store.pest_alerts("chiredzi") for c in a["crops_affected"].split(";")}

    def exposed(hh):
        return any(p["planted_on"] and p["crop"] in pest_crops for p in store.plots(hh))

    flags = [exposed(hh) for hh in order]
    assert any(flags) and flags == sorted(flags, reverse=True)


# --- review fixes -----------------------------------------------------------

DISTRICT_ONLY = submit(("Wait for 20 mm of rain in a dekad before dry planting.", [RAIN]),
                       ("Scout for fall armyworm twice a week.", [PEST]))


@pytest.mark.parametrize("recommendations", [
    ["Scout for armyworm"],                          # bare strings
    [{"text": 5, "citations": [RAIN]}],              # non-string text
    [{"text": "x", "citations": [7]}],               # non-string citation
    "not json at all",
    {"text": "x", "citations": [RAIN]},              # an object, not a list
])
def test_malformed_draft_is_retried_then_dropped_not_a_crash(env, recommendations):
    app, queue, audit, new_items = env
    bad = call("submit_draft", {"recommendations": recommendations})
    graph, _ = app(*GATHER, bad, bad)
    result, pauses = run(graph)
    assert pauses == [] and result["outcome"]["status"] == "dropped"
    assert new_items() == []


def test_draft_sent_as_a_json_string_is_accepted(env):
    # Small models often JSON-encode nested arrays; the content is still checked.
    app, queue, audit, new_items = env
    as_string = call("submit_draft", {"recommendations": json.dumps(
        [{"text": "Scout for fall armyworm twice a week.", "citations": [PEST]}])})
    graph, _ = app(*GATHER, as_string)
    result, pauses = run(graph, [{"decision": "approve", "officer": "T. Moyo", "answered_via": "prompt"}])
    assert result["outcome"]["status"] == "approved" and len(new_items()) == 1


def test_answer_that_is_not_an_object_is_asked_again(env):
    app, queue, audit, new_items = env
    graph, _ = app(*GATHER, GOOD)
    result, pauses = run(graph, ["approve", {"decision": "reject", "officer": "T. Moyo"}])
    assert len(pauses) == 2 and "decision object" in pauses[1]["error"]
    assert result["outcome"]["status"] == "rejected" and len(audit.entries()) == 1


def test_clock_uses_the_time_server_answer(tools, tmp_path):
    # A time server that says 2031: if the graph computed today itself, this fails.
    def fake_time(timezone: str) -> str:
        return json.dumps({"timezone": timezone, "datetime": "2031-01-01T08:00:00+02:00",
                           "day_of_week": "Wednesday", "is_dst": False})

    fake = StructuredTool.from_function(fake_time, name="get_current_time", description="fake")
    swapped = [fake if t.name == "get_current_time" else t for t in tools]
    graph = build_graph(ScriptedModel(script=[call("no_advice", {"reason": "test"})]), swapped,
                        DataStore.load(), _SHARED_QUEUE, AuditLog(tmp_path / "a.jsonl"))
    result = asyncio.run(graph.ainvoke({"household_id": HH}))
    assert result["today"] == "2031-01-01"
    assert asyncio.run(cli.today_from_time_server(swapped)) == date(2031, 1, 1)


def test_demo_refuses_a_scripted_decision_without_an_officer(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "make_model", lambda spec=None: pytest.fail("model started before the check"))
    assert cli.main(["demo", "--decide", "approve"]) == 2
    assert "needs --officer" in capsys.readouterr().err


def _demo_env(monkeypatch, tmp_path, script):
    monkeypatch.setenv("EXTENSION_DESK_QUEUE", str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("EXTENSION_DESK_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setattr(cli, "make_model", lambda spec=None: ScriptedModel(script=script))
    return AuditLog(tmp_path / "a.jsonl")


def test_demo_flag_decisions_are_labelled_as_not_reviewed(monkeypatch, tmp_path):
    audit = _demo_env(monkeypatch, tmp_path, [*GATHER, DISTRICT_ONLY])
    assert cli.main(["demo", "--limit", "1", "--decide", "approve", "--officer", "T. Moyo"]) == 0
    [entry] = audit.entries()
    assert entry["officer"] == "T. Moyo" and entry["answered_via"] == "flag"


def test_demo_asks_until_it_gets_an_officer_name(monkeypatch, tmp_path):
    audit = _demo_env(monkeypatch, tmp_path, [*GATHER, DISTRICT_ONLY])
    answers = iter(["", "  ", "T. Moyo", "a"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.main(["demo", "--limit", "1"]) == 0
    [entry] = audit.entries()
    assert entry["officer"] == "T. Moyo" and entry["answered_via"] == "prompt"


def test_backlog_limit_zero_is_the_whole_district():
    store = DataStore.load()
    assert len(backlog(store, "mangwe", 0, today=date(2026, 10, 12))) == len(store.households("mangwe"))


def test_queue_file_from_before_decisions_is_upgraded(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:  # the schema #10 shipped
        conn.execute("""CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
            household_id TEXT NOT NULL, payload TEXT NOT NULL, citations TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_approval', created_by TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
    queue = ReviewQueue(path)
    item = _pending(queue)
    gate.decide(queue, AuditLog(tmp_path / "a.jsonl"), item["item_id"], "approve", "T. Moyo")
    assert queue.get(item["item_id"])["decided_by"] == "T. Moyo"


def test_ollama_host_without_a_scheme(monkeypatch):
    import importlib

    from extension_desk.agent import models
    monkeypatch.setenv("OLLAMA_HOST", "localhost:11434")
    try:
        assert importlib.reload(models).OLLAMA_URL == "http://localhost:11434"
    finally:
        monkeypatch.delenv("OLLAMA_HOST")
        importlib.reload(models)


# --- Codex review -----------------------------------------------------------

def test_a_second_run_of_the_same_household_must_gather_its_own_evidence(env):
    # Run 1 reads the rainfall and is rejected. Run 2, on the same graph,
    # submits the same citations without reading anything: it must not pass.
    app, queue, audit, new_items = env
    graph, model = app(*GATHER, GOOD,                 # run 1
                       DISTRICT_ONLY, DISTRICT_ONLY)  # run 2: no evidence gathered
    answers = iter([{"decision": "reject", "officer": "T. Moyo", "answered_via": "prompt"}])
    first = asyncio.run(cli.run_household(graph, HH, lambda payload: next(answers)))
    second = asyncio.run(cli.run_household(graph, HH, lambda payload: pytest.fail("reached the gate")))
    assert first["status"] == "rejected"
    assert second["status"] == "dropped" and "no tool returned in this run" in second["reason"]
    assert len(new_items()) == 1


def test_graph_refuses_to_reuse_a_thread(env):
    app, *_ = env
    graph, _ = app(*GATHER, GOOD)
    config = {"configurable": {"thread_id": "same"}}

    async def go():
        await graph.ainvoke({"household_id": HH}, config)  # pauses at the gate
        await graph.ainvoke({"household_id": HH}, config)

    with pytest.raises(RuntimeError, match="new thread_id"):
        asyncio.run(go())


def test_turn_limit_holds_when_the_last_turn_is_a_failed_draft(env):
    # Turn 1 gathers, turns 2-9 are prose, turn 10 is an invalid draft: no turn 11.
    app, queue, audit, new_items = env
    prose = AIMessage("Thinking about it.")
    graph, model = app(GATHER[1], *[prose] * 8, submit(("Plant now.", ["hh:001"])), GOOD)
    result, pauses = run(graph)
    assert model.calls == MAX_TURNS and pauses == []
    assert result["outcome"]["status"] == "dropped" and new_items() == []


def test_turn_limit_holds_when_the_server_refuses_on_the_last_turn(env):
    app, queue, audit, new_items = env
    prose = AIMessage("Thinking about it.")
    graph, model = app(call("get_plot_records", {"district": "chiredzi"}), *[prose] * 8,
                       submit(("Rotate with groundnuts.", ["plot:002-1"])), GOOD)
    result, pauses = run(graph)
    assert model.calls == MAX_TURNS and pauses == []
    assert result["outcome"]["status"] == "dropped" and new_items() == []
