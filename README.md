# Extension Desk

An agentic assistant for agricultural extension offices. An officer can't visit every smallholder, so Extension Desk turns rainfall, pest, price and plot data into **sourced advisory drafts**. A named officer reviews and approves each draft before anything reaches a household.

Built for the [Agentic AI for Africa Innovation Challenge](https://agentic-africa-challenge.lovable.app/tracks/agriculture), Agriculture track: "The Extension Backlog".

> **Status:** in development. Submission deadline is 15 Oct 2026.

## How it works

- **The agent drafts and never sends.** It cannot message a farmer, place an input order or raise a credit flag. Every irreversible action stops at an officer approval gate. Each gate logs inputs, outputs, a timestamp and the approving officer.
- **Every recommendation is sourced.** Each line of advice cites the rainfall, price or plot record it relies on. A recommendation with no source is rejected before an officer ever sees it.
- **Built on MCP.** Our own MCP server exposes the data and drafting tools, and the agent also uses one third-party MCP server (see [ARCHITECTURE.md](ARCHITECTURE.md)).
- **Open source throughout.** Orchestration uses LangGraph, and at least one full task runs on an open-weights model.

## Run it

```bash
# one command; TODO: finalise once the entrypoint exists
uv run extension-desk demo
```

## Data

See [data/README.md](data/README.md) for every source, what it covers, and what it doesn't. No real household records are used. Household and plot data is synthetic.

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md): one-page design
- [EVALS.md](EVALS.md): test tasks, pass/fail results, and known failures

## Licence

Apache-2.0
