# Architecture

> One page. Filled in as the build lands.

## Components

- **Extension Desk MCP server** (ours, `src/extension_desk/mcp_server/`, stdio, `uv run extension-desk-mcp`):
  - Read tools: `get_rainfall`, `get_prices`, `get_plot_records`, `get_pest_alerts`. Each returns the records it used and a `citations` list of their IDs.
  - Acting tools: `draft_advisory`, `flag_for_followup`, `schedule_followup`. Each only writes a `pending_approval` item to the review queue. Every recommendation, flag and visit must cite at least one record that exists and is about that household (the household, one of its plots, or its district's rainfall, price or pest records), or nothing is written.
  - No tool sends, orders, books or approves anything.
- **Review queue** (`src/extension_desk/review_queue.py`, SQLite under `state/`): where drafts wait for an officer. It has no method that changes an item's status; that belongs to the approval gate.
- **Third-party MCP server**: TBD. Add one line on why it's used.
- **Agent**: LangGraph graph. Plan → gather evidence → draft → citation check → officer approval gate → record
- **Model**: hosted model for development; one full task on an open-weights model
- **Audit log**: every gate decision with inputs, outputs, timestamp and approver

## Human-in-the-loop

TBD: where the graph interrupts, what the officer sees, and how approval and rejection are recorded.

## Data flow

TBD
