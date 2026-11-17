"""The officer page: plain-English steps, and the full review through its API."""

import json
import time
from pathlib import Path

import pytest
from langchain_core.messages import messages_from_dict
from starlette.testclient import TestClient

from extension_desk.agent.audit import AuditLog
from extension_desk.agent.replay import load_recording
from extension_desk.officer.app import Desk, create_app
from extension_desk.officer.steps import describe, refusal

RECORDING = Path(__file__).resolve().parents[1] / "evals" / "runs" / "20261012T215559Z-qwen2.5-7b" / "hh_018.json"


def test_refusal_names_the_line_the_wrong_crop_and_what_is_grown():
    text = ("The review queue refused the draft: Error executing tool draft_advisory: recommendation 3 "
            "('Be vigilant'): invalid citations: pest:chiredzi:001: affects sorghum, which hh:018 doesn't grow "
            "(cowpeas, groundnuts)")
    assert refusal(text) == ("Refused by our server: line 3 cites pest alerts for sorghum, "
                             "but hh:018 grows cowpeas, groundnuts")
    assert refusal("The review queue refused the draft: something else").startswith("Refused by our server: something")


def test_recorded_tool_results_read_as_plain_english():
    msgs = messages_from_dict(json.loads(RECORDING.read_text(encoding="utf-8"))["messages"])
    tools = [m for m in msgs if m.type == "tool" and m.name]
    texts = [s["text"] for m in tools for s in describe("tools", {"messages": [m]})]
    assert "3 plots: cowpeas, groundnuts; 1 planted" in texts
    assert "Cowpeas at Rutenga: $0.635/kg (FEWS NET)" in texts
    assert "3 pest alerts: fall armyworm, cutworm, all on sorghum" in texts


@pytest.fixture
def page(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTENSION_DESK_QUEUE", str(tmp_path / "q.sqlite3"))
    monkeypatch.setenv("EXTENSION_DESK_AUDIT", str(tmp_path / "a.jsonl"))
    desk = Desk("chiredzi", load_recording(RECORDING), None, 0.0, runs_dir=tmp_path / "runs")
    with TestClient(create_app(desk)) as client:
        yield client, AuditLog(tmp_path / "a.jsonl")


def _wait(client, run_id, states=("gate", "done", "diverged", "error")):
    for _ in range(200):
        r = client.get(f"/api/run/{run_id}").json()
        if r["state"] in states:
            return r
        time.sleep(0.1)
    raise AssertionError(f"run stuck in {r['state']}")


def test_review_on_the_page_is_recorded_under_the_officers_name(page):
    client, audit = page
    info = client.get("/api/info").json()
    assert info["households"][0]["id"] == "hh:018" and info["households"][0]["replay"] is True

    run = client.post("/api/run", json={"household": "hh:018"}).json()
    assert client.post("/api/run", json={"household": "hh:020"}).status_code == 409  # one at a time
    r = _wait(client, run["id"])
    assert r["state"] == "gate", r["error"]
    assert any(s["kind"] == "bad" and "sorghum" in s["text"] for s in r["steps"])  # refusal made live
    assert r["gate"]["item"]["status"] == "pending_approval" and audit.entries() == []

    no_name = client.post(f"/api/run/{run['id']}/decide", json={"decision": "approve", "officer": " "})
    assert no_name.status_code == 409 and "name" in no_name.json()["error"]
    client.post(f"/api/run/{run['id']}/decide", json={"decision": "approve", "officer": "T. Moyo"})
    r = _wait(client, run["id"], ("done", "diverged", "error"))
    assert r["state"] == "done" and r["outcome"]["status"] == "approved" and r["outcome"]["officer"] == "T. Moyo"
    assert r["tokens"]["model_calls"] == 8
    [entry] = audit.entries()
    assert entry["officer"] == "T. Moyo" and entry["outputs"]["delivered"] is False


def test_page_refuses_unknown_households(page):
    client, _ = page
    r = client.post("/api/run", json={"household": "hh:999"})
    assert r.status_code == 409 and "not a household" in r.json()["error"]


def test_the_page_itself_loads(page):
    client, _ = page
    r = client.get("/")
    assert r.status_code == 200 and "Prepare draft" in r.text and "officerName" in r.text


def test_rainfall_and_prices_keep_their_provenance():
    msgs = messages_from_dict(json.loads(RECORDING.read_text(encoding="utf-8"))["messages"])
    texts = [s["text"] for m in msgs if m.type == "tool" and m.name for s in describe("tools", {"messages": [m]})]
    assert any(t.startswith("Rain ") and t.endswith("(CHIRPS, preliminary)") for t in texts)
    assert "Cowpeas at Rutenga: $0.635/kg (FEWS NET)" in texts


def _to_gate(client):
    run = client.post("/api/run", json={"household": "hh:018"}).json()
    r = _wait(client, run["id"])
    assert r["state"] == "gate", r["error"]
    return run["id"]


def test_edit_and_reject_are_recorded_as_such(page):
    client, audit = page
    run_id = _to_gate(client)
    client.post(f"/api/run/{run_id}/decide", json={"decision": "edit", "officer": "T. Moyo",
                                                    "edits": ["Scout the cowpeas weekly."]})
    r = _wait(client, run_id, ("done", "diverged", "error"))
    assert r["outcome"]["decision"] == "edit"
    assert r["outcome"]["final_payload"]["recommendations"][0]["text"] == "Scout the cowpeas weekly."

    run_id = _to_gate(client)
    client.post(f"/api/run/{run_id}/decide", json={"decision": "reject", "officer": "T. Moyo", "note": "too vague"})
    r = _wait(client, run_id, ("done", "diverged", "error"))
    assert r["outcome"]["status"] == "rejected"
    assert [e["decision"] for e in audit.entries()] == ["edit", "reject"]


def test_requests_from_another_site_or_not_json_are_refused(page):
    client, audit = page
    run_id = _to_gate(client)
    forged = client.post(f"/api/run/{run_id}/decide", json={"decision": "approve", "officer": "Forged"},
                         headers={"Origin": "http://evil.example"})
    form = client.post(f"/api/run/{run_id}/decide", content='{"decision": "approve", "officer": "Forged"}',
                       headers={"Content-Type": "text/plain"})
    assert forged.status_code == 403 and form.status_code == 403
    assert client.get(f"/api/run/{run_id}").json()["state"] == "gate" and audit.entries() == []
    ok = client.post(f"/api/run/{run_id}/decide", json={"decision": "approve", "officer": "T. Moyo"},
                     headers={"Origin": "http://testserver"})
    assert ok.status_code == 200
