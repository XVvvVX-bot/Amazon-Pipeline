# Steam Review Pipeline Agent Guide

Read this file before making changes in this repository.

## Project Objective

This project is a live Steam review acquisition pipeline. It collects public, structured review JSON from `store.steampowered.com/appreviews/{app_id}`, normalizes full written review rows, stores them in Postgres, and publishes per-run raw/report artifacts through GitHub Actions.

## Ethical Boundaries

Keep acquisition work inside these boundaries:

- Use documented public endpoints only.
- Do not use login-required pages.
- Do not use personal cookies, browser sessions, or account state.
- Do not solve, bypass, or automate around access controls.
- Do not use proxy rotation or identity rotation.
- Do not use hidden APIs or anti-bot evasion.
- Do not store raw Steam user IDs in normalized Postgres tables or exports; Steam review identity is `recommendationid`.
- Keep raw artifacts sanitized so reviewer identity metadata is not retained unnecessarily.

## Architecture

The pipeline is staged:

1. Read app targets from `data/targets/steam_apps.csv`.
2. Fetch public Steam review JSON pages with cursor pagination.
3. Save sanitized raw JSON pages and page metadata.
4. Normalize full written review rows.
5. Upsert normalized rows into Postgres by `recommendationid`.
6. Validate database quality.
7. Update per-app sync state only when an app reaches a complete terminal reason.
8. Publish raw/report workflow artifacts from GitHub Actions.

The scheduled workflow runs on the local Mac self-hosted runner and uses local Postgres by default. No browser automation is required.

## Core Commands

Run tests:

```bash
TEST_DATABASE_URL=postgresql:///steam_reviews_test .venv/bin/python -m pytest -q
```

Run daily acquisition locally:

```bash
.venv/bin/python steam_pipeline.py daily
```

Run a full public backfill:

```bash
.venv/bin/python steam_pipeline.py daily --review-filter recent --max-pages-per-app 0
```

Validate the Postgres database:

```bash
.venv/bin/python steam_pipeline.py validate-postgres
```

Export reviews only for ad hoc analyst extracts:

```bash
.venv/bin/python steam_pipeline.py export-postgres --format jsonl --output /tmp/steam_reviews.jsonl
```

## Rules For Future Agents

- Inspect recent reports before changing acquisition strategy.
- Preserve the staged pipeline boundaries: target selection, fetch, load, validate, report.
- Keep live network calls out of the test suite; use fixtures and fake sessions.
- Do not introduce a reviewer/user table. The review is the primary unit of data.
- Do not advance `steam_app_sync_state.complete_through_timestamp_updated` after an incomplete stop such as a page cap, runtime cap, fetch error, or cursor issue.
- Avoid hardcoded local machine paths in code or workflows.
- Do not commit large raw JSON, database, export, or artifact outputs unless explicitly requested.
- Prefer improving reports, observability, and reproducibility before adding downstream modeling.

## Key Files

- `README.md`: command reference and architecture overview.
- `steam_pipeline.py`: CLI wrapper.
- `steam_review_pipeline/`: Steam target loading, fetch, load, validation, export, and daily orchestration.
- `data/targets/steam_apps.csv`: curated target list.
- `.github/workflows/steam-daily-pipeline.yml`: scheduled/manual acquisition automation.
- `.github/workflows/ci.yml`: test workflow.
- `docs/project_context.md`: current project direction and open decisions.
- `docs/operations_runbook.md`: operating and troubleshooting guide.
