# Architecture

> One page. Filled in as the build lands.

## Components

- **Extension Desk MCP server** (ours): data tools (rainfall, prices, plot records) and acting tools (draft advisory, flag for follow-up, schedule follow-up)
- **Third-party MCP server**: TBD. Add one line on why it's used.
- **Agent**: LangGraph graph. Plan → gather evidence → draft → citation check → officer approval gate → record
- **Model**: hosted model for development; one full task on an open-weights model
- **Audit log**: every gate decision with inputs, outputs, timestamp and approver

## Human-in-the-loop

TBD: where the graph interrupts, what the officer sees, and how approval and rejection are recorded.

## Data flow

TBD
