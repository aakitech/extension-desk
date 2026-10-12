# Open-weights run: qwen2.5:7b, 12 Oct 2026

One full backlog pass on an open-weights model, end to end: evidence through our MCP server, draft, citation check, server-side check, and the officer approval gate. This is the brief's "one full task run on an open-weights model" (#4).

- **Model:** `qwen2.5:7b` through Ollama 0.40.3 (7.6B parameters, Q4_K_M, digest `845dbda0ea48`), run locally on an RTX 3050 6 GB laptop GPU, temperature 0.
- **Code:** commit `8a33b60` (`agent` branch, PR #11).
- **Command:**
  ```
  uv run extension-desk demo --limit 3 --decide reject --officer "automated run (no human review)"
  ```
  The queue, audit log and transcripts went to a fresh scratch directory (`EXTENSION_DESK_QUEUE`, `_AUDIT`, `_RUNS`), so no earlier state is mixed in.
- **Officer gate:** answered by `--decide reject`. Every decision is logged with `answered_via: "flag"` under an officer name that says no one reviewed it. Nothing was approved and nothing was sent.
- **"Today":** 2026-10-12, from the reference `time` MCP server.

## Files

| File | What it is |
|---|---|
| `run.json` | Command, model build, commit, date, per-household outcome and timing |
| `hh_003.json`, `hh_018.json`, `hh_016.json` | Every message and tool call, the gate pause (what the officer was shown) and the answer, the outcome |
| `queue.json` | The review queue after the run |
| `audit.jsonl` | The append-only audit log after the run |
| `console.txt` | Terminal output. MCP server request-log lines are filtered out |

The only edit to the recorded files: local paths in `run.json` and `console.txt` are replaced with `<repo>` and `<scratch>`, because this repo is public.

## What happened

| Household | Outcome | Time | Model turns | Failed drafts | What stopped or fixed it |
|---|---|---|---|---|---|
| hh:003 (sorghum, one irrigated plot planted 7 Sep) | reached the officer, rejected | 71.5 s | 6 | 1 | **Citation check:** recommendation 4 cited only the household record. It resubmitted with evidence. |
| hh:018 (cowpeas planted 22 Sep, groundnuts) | reached the officer, rejected | 108.5 s | 8 | 1 | **Server check:** the draft cited a sorghum fall-armyworm alert. The model read the refusal, said "the household does not grow sorghum", removed the line and resubmitted. |
| hh:016 (maize, irrigated groundnuts planted 17 Sep) | dropped, nothing queued | 56.6 s | 6 | 2 | **Server check:** sorghum pest alerts for a household that grows none. Then the **citation check**: "recommendation 4 has no citations". Both retries were used up. |

All three households called the four read tools in the same order: plots, rainfall, prices, pests. The model chose its own arguments.

## What it shows about the model

These observations come from the transcripts:

- **It asks for data that doesn't exist yet.** For hh:003 and hh:016 it requested rainfall for 1-12 October, but the data ends with the last September dekad. The tool returned null totals and a note naming the latest dekad. Neither final draft for those households cites rainfall at all.
- **It sends arguments the tools don't take** (`includes_preliminary: true` on `get_rainfall`). The server ignored them.
- **It gets details wrong.** The hh:003 draft calls cutworm "high severity"; the cited record says medium. It also tells a household with nothing harvested yet to "consider selling sorghum when prices are favorable".
- **Its drafts are generic.** hh:003's draft packs three pieces of advice into one sentence. hh:018's final draft is one line: "Monitor rainfall and irrigation, keep an eye on market trends, and manage the cowpeas and groundnuts plots."
- **It responds to the guards.** Each refusal message names the bad citation and why. On hh:018 the model fixed the exact problem on the next turn.

## Not compared

No hosted-model run was made, so this run says nothing about how a hosted model would differ. The earlier interactive run on the same model and data (12 Oct, before transcripts were recorded) gave an identical hh:003 draft, as expected at temperature 0.
