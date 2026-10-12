# Extension Desk — Agent Rules

Challenge entry with a hard deadline: **15 Oct 2026**. Repos are cloned at the deadline timestamp, and commits after it disqualify the entry. Brief: https://agentic-africa-challenge.lovable.app/tracks/agriculture

## Non-negotiables (instant disqualification if broken)

- The agent **drafts, never sends**. No tool may message a farmer, place an order or raise a credit flag without an officer approval gate, and every gate logs inputs, outputs, timestamp and approver.
- **Every recommendation cites its source record.** Unsourced output is a bug, not a style issue.
- **No real household data.** Use public datasets plus synthetic households and plots only. This repo is public, so no AakiTech client, partner or field data goes in it, ever.
- No fabricated results. `EVALS.md` reports what actually ran, including at least one unfixed failure.
- The README must run in one command.

## Required pieces (from the brief)

- Our own MCP server with **3 or more tools**, at least one of which acts (drafts, flags or schedules)
- **One MCP server we didn't write**, with a line in ARCHITECTURE.md explaining why
- Open-source orchestration (LangGraph). No closed no-code builders
- **One full task run on an open-weights model**

## How we work this week

- PRs are deliberately large; there's no time for small ones. Each PR still gets one review before merge.
- Every PR states which brief requirement it satisfies.
- Run `uv run pytest` before pushing.

## Layout

- `src/extension_desk/mcp_server/`: our MCP server and its tools
- `src/extension_desk/agent/`: LangGraph orchestration, approval gate, audit log
- `data/`: public extracts and the synthetic dataset, with provenance in `data/README.md`
- `evals/`: eval tasks and harness feeding `EVALS.md`
