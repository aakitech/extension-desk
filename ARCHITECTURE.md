# Architecture

## Components

- **Extension Desk MCP server** (ours, `src/extension_desk/mcp_server/`, stdio, `uv run extension-desk-mcp`):
  - Read tools: `get_rainfall`, `get_prices`, `get_plot_records`, `get_pest_alerts`. Each returns the records it used and a `citations` list of their IDs.
  - Acting tools: `draft_advisory`, `flag_for_followup`, `schedule_followup`. Each only writes a `pending_approval` item to the review queue. Every recommendation, flag and visit must cite at least one record that exists and is about that household (the household, one of its plots, or its district's rainfall, price or pest records), or nothing is written.
  - No tool sends, orders, books or approves anything.
- **Third-party MCP server: the reference [`time` server](https://github.com/modelcontextprotocol/servers/tree/main/src/time)** (`mcp-server-time`, pinned). We use it because "today" drives the advice: which dekads count as recent, whether a proposed visit date is valid, and where the household is in the season. A model's own sense of the date is unreliable, and hard-coding it would make the demo lie on any other day.
- **Agent** (`src/extension_desk/agent/graph.py`, LangGraph): one household per run. The model chooses which read tools to call and in what order; the graph enforces everything that must not be left to the model.
- **Model** (`agent/models.py`): chosen by config. The default is `ollama:qwen2.5:7b`, which is local and open-weights, so the demo needs no API key. `anthropic:<model>` is the hosted option (`uv sync --extra hosted`).
- **Review queue** (`src/extension_desk/review_queue.py`, SQLite under `state/`): where drafts wait for an officer. Only `decide()` changes an item's status, once, and only the approval gate calls it. The MCP server doesn't expose it.
- **Audit log** (`agent/audit.py`, `state/audit.jsonl`): append-only JSONL. It records every officer decision with inputs, outputs, timestamp and officer, plus every dropped draft and every "no advice".

## The graph

```
clock ─► agent ◄──► tools
           │
           ├─ submit_draft ─► citation check ─► queue draft (MCP) ─► officer gate ─► end
           │                       └── fail: back to agent once, then drop (logged)
           ├─ no_advice ─► logged, end
           └─ too many turns ─► drop (logged)
```

- **clock**: `get_current_time(Africa/Harare)` from the time server.
- **agent / tools**: the model calls read tools. Every record ID they return is collected as "seen this run".
- **citation check**: every recommendation must have text, cite only IDs a tool returned *in this run*, and cite at least one `rain:`, `price:`, `pest:` or `plot:` record. A real record the model never looked up fails the check. On failure the model gets the reasons and one more try, then the draft is dropped.
- **queue draft**: calls the MCP `draft_advisory` tool, which runs its own check (each citation must be about this household) before writing a pending item. So there are two independent checks.

## Human-in-the-loop

- **Where it stops:** the graph pauses at `officer_gate` with LangGraph `interrupt`. Nothing is approved without an officer's answer.
- **What the officer sees:** the draft, and every cited record in full, with its source. In the demo this is printed to the terminal.
- **What the officer can do:** approve, edit (rewrite or remove lines; citations stay attached) or reject. A decision needs the officer's name. A malformed answer is asked again, and nothing is recorded until a valid one arrives.
- **How it's recorded:** `gate.decide` moves the item from `pending_approval` to `approved` or `rejected` exactly once. The audit entry (inputs, outputs, timestamp, officer) is written inside the same SQLite transaction. If the log write fails, the decision rolls back. An item is never decided twice.
- **Delivery:** approval means "signed off", not "sent". Delivery to the household is the officer's own step, outside this system (`delivered: false` in every entry).

## Data flow

`data/*.csv` (public CHIRPS/WFP/FEWS NET extracts plus synthetic households) → `DataStore` → MCP read tools → model → draft → citation check → MCP `draft_advisory` → review queue → officer gate → audit log.

The agent process reads `DataStore` directly only to show the officer each cited record.
