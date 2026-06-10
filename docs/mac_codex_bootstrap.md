# Mac Codex Bootstrap

This file is for the Codex session that will run on the MacBook.

## First Files To Read

Read these files before deciding what to install or change:

1. `AGENTS.md`
2. `README.md`
3. `docs/project_context.md`
4. `docs/operations_runbook.md`

## First Health Check

After reading the files, inspect the repository and run the test suite:

```bash
python -m pytest -q
```

If `python` is not the right command on the MacBook, choose the appropriate local Python 3.12 command for that machine.

## Before Strategic Changes

Before changing discovery, fetching, parsing, queueing, or storage strategy:

1. Inspect the latest GitHub Actions run.
2. Download or inspect the latest artifact if needed.
3. Read `daily_report.json`, `discovery_report.json`, and `validation_report.json`.
4. Identify whether the issue is discovery, fetch access, parser coverage, database loading, or workflow infrastructure.

## Suggested First Prompt

Use this prompt in the MacBook Codex session:

```text
Read AGENTS.md, README.md, docs/project_context.md, and docs/operations_runbook.md. Then inspect the current repository state and tell me what must be configured on this MacBook before it can become the self-hosted acquisition runner.
```

## Important Boundary

The MacBook can be used as the self-hosted acquisition runner, but the architecture should stay portable to a future company VM. Avoid local absolute paths, personal browser state, and machine-specific assumptions in committed code.
