# evals/

- `runs/`: recorded demo runs (the open-weights run for #4).
- `results/<run>/`: one directory per `extension-desk eval` run, each with `results.json` and one transcript, queue and audit log per task. Committed as generated.

The tasks, fixtures and harness are code, so they live in the package: `src/extension_desk/evals/` (`tasks.py`, `fixtures.py`, `harness.py`). Run them with `uv run extension-desk eval`. See [EVALS.md](../EVALS.md) for the latest results.
