# Extension Desk — Agent Rules

Entry for the Agentic AI for Africa Innovation Challenge, Agriculture track ("The Extension Backlog"). **Hard deadline: 15 Oct 2026.** Repos are cloned at the deadline timestamp, and commits after it disqualify the entry.

Brief: https://agentic-africa-challenge.lovable.app/tracks/agriculture. Read it before any design decision; it is the spec and the rubric.

**This repo is public.** Nothing confidential goes in: no client or partner names, no field data, no internal funding or strategy material.

## Start of every session

1. **Note the real current date and time against the deadline.** Every decision here is a trade-off against 15 Oct.
2. **Which issue?** Read it, including comments, before starting. The milestones (`M1: Working agent`, `M2: Submission`) are the plan.
3. **Check the open PRs.** Work is moving in parallel, so don't duplicate a branch someone already has.
4. **Re-read the non-negotiables below** if the task touches tools, the agent graph or data.

Re-run this on every task switch, not just at chat start.

## Non-negotiables (each is an instant disqualification in the brief)

- The agent **drafts, never sends**. No tool may message a farmer, place an order or raise a credit flag without an officer approval gate. Every gate logs inputs, outputs, timestamp and approver.
- **Every recommendation cites its source record.** Unsourced output is a bug, not a style issue.
- **No real household data.** Public datasets plus synthetic households and plots only.
- **No fabricated results.** `EVALS.md` reports only what actually ran, including at least one unfixed failure.
- **The README runs in one command.**
- Not a chatbot wrapper, and the MCP server doesn't just proxy one endpoint.

## Required pieces (from the brief)

- Our own MCP server with **3 or more tools**, at least one of which acts (drafts, flags or schedules)
- **One MCP server we didn't write**, with a line in `ARCHITECTURE.md` explaining why
- Open-source orchestration (LangGraph). No closed no-code builders
- **One full task run on an open-weights model**
- Deliverables: README, demo video under 3 minutes, a ~300-word description, `ARCHITECTURE.md`, `EVALS.md` (8 or more tasks, one unfixed failure)

Judging (100 points): agentic depth & MCP craft 30 · open-source rigour 20 · fit to farming cycle 20 · evaluation & reliability 15 · defensibility 10 · demo 5. The live review adds **a new requirement on the spot**, so keep the code easy to change.

## Team

| Person | GitHub | Focus |
|---|---|---|
| Brighton | @dev-thandabantu | Lead; data layer, MCP server, agent |
| Munashe | @dev-munashe | Open-weights run, reviews |
| Law | @law001-mugah | Submission, demo, live-review prep |

## Stack and commands

Python 3.11+, [uv](https://docs.astral.sh/uv/), the official MCP Python SDK, LangGraph.

```bash
uv sync                 # install
uv run pytest           # full gate; run before every push
```

If `uv` isn't on PATH, use `python -m uv`.

## Layout

- `data/`: committed CSVs, with provenance in `data/README.md`. Every row has a typed `record_id` (`rain:`, `price:`, `hh:`, `plot:`, `pest:`)
- `scripts/`: regenerate the data (`fetch_*`, `generate_synthetic.py`)
- `src/extension_desk/data.py`: `DataStore`, read-only lookups. Everything else reads data through it
- `src/extension_desk/mcp_server/`: our MCP server and its tools
- `src/extension_desk/agent/`: LangGraph orchestration, approval gate, audit log
- `evals/`: eval tasks and harness feeding `EVALS.md`

## How we work this week

- One branch and one PR per issue. Large PRs are fine; there's no time for small ones.
- Every PR names the brief requirement it satisfies, and uses the PR template.
- **Before requesting review:**
  1. Run your own adversarial pass: what would still pass if the feature were deleted, and which PR claims don't match the code.
  2. Get a fresh-context review (Claude subagent or new session), given only the PR and its issue.
  3. **Get a Codex review.** Brighton runs it by hand: write him a ready-to-paste prompt that gives only the PR URL, the issue, and "find real problems, give file/line, a concrete case, blocking or discussion; don't change files".
  4. Fix what holds up, then post **one consolidated comment**: what each review found, what was fixed, what was left.
- **A human approves and merges.** Agents don't merge.
- The PR that implements the approval gate must state the invariant ("nothing reaches a household without a logged officer approval") in its description and test it directly.

## Keeping this file current

Update it when the brief's requirements are clarified, the team changes, or a convention is set that later sessions need. Live status (what's in progress, what's next) belongs in issues and PRs, not here.
