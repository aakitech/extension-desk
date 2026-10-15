# Extension Desk

An agentic assistant for agricultural extension offices. An officer can't visit every smallholder, so Extension Desk turns rainfall, pest, price and plot data into **sourced advisory drafts**. A named officer reviews and approves each draft before anything reaches a household.

Built for the [Agentic AI for Africa Innovation Challenge](https://agentic-africa-challenge.lovable.app/tracks/agriculture), Agriculture track: "The Extension Backlog".

> **Status:** challenge submission, 15 Oct 2026. Results and known failures are in [EVALS.md](EVALS.md).

## How it works

- **The agent drafts and never sends.** It cannot message a farmer, place an input order or raise a credit flag. Every irreversible action stops at an officer approval gate. Each gate logs inputs, outputs, a timestamp and the approving officer.
- **Every recommendation is sourced.** Each line of advice cites the rainfall, price or plot record it relies on. A recommendation with no source is rejected before an officer ever sees it.
- **Built on MCP.** Our own MCP server exposes the data and drafting tools, and the agent also uses one third-party MCP server (see [ARCHITECTURE.md](ARCHITECTURE.md)).
- **Open source throughout.** Orchestration uses LangGraph, and at least one full task runs on an open-weights model.

## Run it

```bash
uv run extension-desk demo
```

This runs one backlog pass over the Chiredzi pilot district. For each of three households, the agent gathers evidence through our MCP server, drafts an advisory with citations, and stops at the officer approval gate. Each tool call the model makes is printed as it happens, with what came back and what the citation checks decided (`--quiet` turns this off). You answer as the officer (approve, edit or reject). Nothing is sent to any household. Decisions go to `state/audit.jsonl`, and a full transcript of the run to `state/runs/`.

Before you run it:

- [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) with the default open-weights model: `ollama pull qwen2.5:7b`. If Ollama isn't running, the demo says so and exits.

Options: `--quiet`, `--district mangwe`, `--limit N`, `--officer NAME`, `--decide approve|reject` (non-interactive), `--model anthropic:<model>` (hosted; run `uv sync --extra hosted` and set `ANTHROPIC_API_KEY`).

## Evals

```bash
uv run extension-desk eval                 # 11 tasks on the configured model, about 15 minutes on a laptop GPU
uv run extension-desk eval --update-evals  # also rewrite the results table in EVALS.md
```

Each task runs the real agent and MCP servers, often on a modified copy of the data (a drought, missing records, a hostile data field), and checks the outcome automatically. Results and transcripts go to `evals/results/`. See [EVALS.md](EVALS.md) for the latest run and the known failures.

## Data

See [data/README.md](data/README.md) for every source, what it covers, and what it doesn't. No real household records are used. Household and plot data is synthetic.

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md): one-page design
- [EVALS.md](EVALS.md): test tasks, pass/fail results, and known failures

## Licence

Apache-2.0
