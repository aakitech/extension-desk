# Evals

> At least 8 test tasks with pass/fail results, and at least one unfixed failure with next steps. Results here must come from actual runs.

## Results

The table between the markers is written by `uv run extension-desk eval --update-evals` from an actual run (#5). Don't edit it by hand.

<!-- results:start -->
Not run yet.
<!-- results:end -->

## Open-weights run

One full backlog pass on `qwen2.5:7b` (Ollama, Q4_K_M, local GPU), recorded in [`evals/runs/20261012T215559Z-qwen2.5-7b/`](evals/runs/20261012T215559Z-qwen2.5-7b/README.md) with the command, model digest, commit, and every message and tool call.

| Household | Outcome | Failed drafts | Caught by |
|---|---|---|---|
| hh:003 | reached the officer (rejected by `--decide`) | 1 | citation check: a line cited only the household record |
| hh:018 | reached the officer (rejected by `--decide`) | 1 | server check: a sorghum pest alert for a household that grows none; the model fixed it |
| hh:016 | dropped, nothing queued | 2 | server check (same sorghum alert), then citation check (a line with no citations) |

What the run showed about this model:

- **It needs the guards.** Every household's first draft failed a check. One of three ran out of retries.
- **It asks for data that doesn't exist yet** (October rainfall), and then drafts without citing rainfall.
- **It gets details wrong that the citation checks can't see.** It calls a medium-severity alert "high". It suggests selling a crop that hasn't been harvested.
- **Its drafts are generic.**

**Not compared:** no hosted-model run has been made, so this records no differences from a hosted model.

## Unfixed failure

To be written from the #5 eval run.
