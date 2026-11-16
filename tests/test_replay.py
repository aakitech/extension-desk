"""`demo --replay` and `demo --household`: the replayed model meets the live
servers and checks, and a replay that no longer fits the live system stops."""

import csv
import json
import shutil
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from extension_desk.agent import cli
from extension_desk.agent.audit import AuditLog
from extension_desk.agent.graph import QUEUE_REFUSED
from extension_desk.agent.replay import first_difference, load_recording, token_usage
from extension_desk.data import DATA_DIR

RECORDING = Path(__file__).resolve().parents[1] / "evals" / "runs" / "20261012T215559Z-qwen2.5-7b" / "hh_018.json"


@pytest.fixture
def demo_env(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTENSION_DESK_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("EXTENSION_DESK_QUEUE", str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("EXTENSION_DESK_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setattr(cli, "make_model", lambda spec=None: pytest.fail("a replay must not start a model"))
    return tmp_path


def _replay(*extra):
    return cli.main(["demo", "--replay", str(RECORDING), "--replay-delay", "0",
                     "--decide", "approve", "--officer", "T. Moyo", *extra])


def _transcript(tmp_path) -> dict:
    [path] = (tmp_path / "runs").glob("*/hh_018.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_replay_reaches_the_officer_and_the_refusal_is_made_live(demo_env, capsys):
    assert _replay() == 0
    out = capsys.readouterr().out
    assert "REPLAY: the model's turns are replayed" in out
    assert "Tokens: 8 model calls, 21,385 in + 2,095 out" in out

    # The refusal comes from the live server's check: the replayed model only
    # supplies AI turns, so a verdict in the live transcript was made now.
    live = _transcript(demo_env)
    verdicts = [m["data"]["content"] for m in live["messages"] if m["type"] == "human"
                and m["data"]["content"].startswith("The review queue refused")]
    assert len(verdicts) == 1 and "affects sorghum, which hh:018 doesn't grow" in verdicts[0]

    [entry] = AuditLog(demo_env / "a.jsonl").entries()
    assert entry["event"] == "officer_decision" and entry["officer"] == "T. Moyo"
    assert entry["outputs"]["delivered"] is False
    assert live["outcome"]["status"] == "approved"


def test_replay_stops_when_a_tool_result_no_longer_matches(demo_env, monkeypatch, capsys):
    # Changed pest data changes what get_pest_alerts returns, so the recorded
    # answers no longer fit what the model would see.
    data = demo_env / "data"
    shutil.copytree(DATA_DIR, data)
    path = data / "pest_alerts.csv"
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["crops_affected"] = "cowpeas"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    monkeypatch.setenv("EXTENSION_DESK_DATA", str(data))

    assert _replay() == 3
    assert "replay stopped" in capsys.readouterr().err
    assert AuditLog(demo_env / "a.jsonl").entries() == []


@pytest.mark.parametrize("args, message", [
    (["--model", "ollama:qwen2.5:7b"], "can't be combined with --model"),
    (["--household", "hh:003"], "is for hh:018 only"),
    (["--district", "mangwe"], "not a household in mangwe"),
])
def test_replay_refuses_conflicting_options(demo_env, capsys, args, message):
    assert _replay(*args) == 2
    assert message in capsys.readouterr().err


def test_unknown_household_is_refused_before_anything_starts(demo_env, capsys):
    assert cli.main(["demo", "--household", "hh:999", "--decide", "approve", "--officer", "T. Moyo"]) == 2
    assert "Not a household in chiredzi: hh:999" in capsys.readouterr().err


def test_household_flag_runs_only_that_household(demo_env, monkeypatch, capsys):
    from test_agent import DISTRICT_ONLY, GATHER, ScriptedModel
    monkeypatch.setattr(cli, "make_model", lambda spec=None: ScriptedModel(script=[*GATHER, DISTRICT_ONLY]))
    monkeypatch.setattr(cli, "model_details", lambda spec=None: {"provider": "scripted"})
    assert cli.main(["demo", "--household", "hh:018", "--decide", "reject", "--officer", "T. Moyo"]) == 0
    out = capsys.readouterr().out
    assert "Households: hh:018\n" in out and "=== hh:018 ===" in out
    assert "Tokens: not reported by this model." in out


def _conversation(tool_result="r", verdict=f"{QUEUE_REFUSED}: x", nudge="Continue."):
    call = AIMessage("", tool_calls=[{"name": "get_plot_records", "args": {"household_id": "hh:018"},
                                      "id": "1", "type": "tool_call"}])
    return [SystemMessage("s"), HumanMessage("Household hh:018. Today is ..."), call,
            ToolMessage(tool_result, tool_call_id="1"), HumanMessage(verdict), HumanMessage(nudge),
            AIMessage("next")]


@pytest.mark.parametrize("change, differs", [
    ({}, False),
    ({"nudge": "Your last reply was plain text."}, False),   # an instruction, not evidence
    ({"tool_result": "other"}, True),
    ({"verdict": f"{QUEUE_REFUSED}: y"}, True),
])
def test_first_difference_compares_evidence_not_instructions(change, differs):
    recorded = _conversation()
    live = _conversation(**change)[:-1]
    assert (first_difference(live, recorded) is not None) is differs


def test_replay_past_the_end_of_the_recording_is_a_difference():
    recorded = _conversation()
    assert "past the end" in first_difference(recorded, recorded)


def test_token_usage_sums_model_replies_only():
    rec = load_recording(RECORDING)
    assert token_usage(rec["messages"]) == {"model_calls": 8, "input_tokens": 21385, "output_tokens": 2095}
    assert rec["model"] == "ollama:qwen2.5:7b"


def test_replay_stops_before_the_officer_when_the_server_now_accepts_a_refused_draft(demo_env, capsys):
    # Tool results stay identical; only the verdict flips. The recorded first
    # draft is changed so line 3 cites the household's own plot: the live
    # server accepts it, and the graph goes straight to the officer without
    # asking the model again. The officer must never see that draft.
    data = json.loads(RECORDING.read_text(encoding="utf-8"))
    first = next(m for m in data["messages"] if m["type"] == "ai"
                 and any(tc["name"] == "submit_draft" for tc in m["data"]["tool_calls"]))
    for tc in first["data"]["tool_calls"]:
        tc["args"]["recommendations"][2]["citations"] = ["plot:018-1"]
    run_dir = demo_env / "rec"
    run_dir.mkdir()
    (run_dir / "hh_018.json").write_text(json.dumps(data), encoding="utf-8")

    # Interactive (--decide ask): if the gate were reached, input() would be
    # called and fail under pytest's captured stdin.
    assert cli.main(["demo", "--replay", str(run_dir / "hh_018.json"), "--replay-delay", "0",
                     "--officer", "T. Moyo"]) == 3
    captured = capsys.readouterr()
    assert "replay stopped" in captured.err and "different path" in captured.err
    assert "Officer decision" not in captured.out and "paused for the officer" not in captured.out
    assert AuditLog(demo_env / "a.jsonl").entries() == []


def test_replay_refuses_a_file_that_is_not_a_transcript(demo_env, capsys):
    bad = demo_env / "bad.json"
    bad.write_text(json.dumps({"household_id": "hh:018", "messages": [{"no": "type"}]}), encoding="utf-8")
    assert cli.main(["demo", "--replay", str(bad), "--decide", "approve", "--officer", "T. Moyo"]) == 2
    assert "Can't load the recording" in capsys.readouterr().err
