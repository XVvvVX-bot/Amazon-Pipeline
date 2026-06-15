# Steam Review Pipeline Agent Guide

Read this file before making changes in this repository.

## Project Objective

This project is a live Steam review acquisition pipeline. It collects public, structured review JSON from `store.steampowered.com/appreviews/{app_id}`, normalizes full written review rows, stores them in SQLite, exports CSV, and publishes daily artifacts through GitHub Actions.

## Ethical Boundaries

Keep acquisition work inside these boundaries:

- Use documented public endpoints only.
- Do not use login-required pages.
- Do not use personal cookies, browser sessions, or account state.
- Do not solve, bypass, or automate around access controls.
- Do not use proxy rotation or identity rotation.
- Do not use hidden APIs or anti-bot evasion.
- Do not store raw Steam user IDs in normalized SQLite tables or exports; Steam review identity is `recommendationid`.
- Keep raw artifacts sanitized so reviewer identity metadata is not retained unnecessarily.

## Architecture

The pipeline is staged:

1. Read app targets from `data/targets/steam_apps.csv`.
2. Fetch public Steam review JSON pages with cursor pagination.
3. Save sanitized raw JSON pages and page metadata.
4. Normalize full written review rows.
5. Load normalized rows into SQLite.
6. Validate database quality.
7. Export cumulative reviews to CSV.
8. Publish workflow artifacts and `latest-steam-data` release assets from GitHub Actions.

The scheduled workflow runs on GitHub-hosted `ubuntu-latest`; no local runner or browser automation is required.

## Core Commands

Run tests:

```bash
python -m pytest -q
```

Run daily acquisition locally:

```bash
python steam_pipeline.py daily
```

Run a full public backfill:

```bash
python steam_pipeline.py daily --review-filter recent --max-pages-per-app 0
```

Validate the SQLite database:

```bash
python steam_pipeline.py validate --db data/steam_reviews.sqlite
```

Export reviews:

```bash
python steam_pipeline.py export --db data/steam_reviews.sqlite --format csv --output data/exports/steam_reviews.csv
```

## Rules For Future Agents

- Inspect recent reports before changing acquisition strategy.
- Preserve the staged pipeline boundaries: target selection, fetch, load, validate, export.
- Keep live network calls out of the test suite; use fixtures and fake sessions.
- Do not introduce a reviewer/user table. The review is the primary unit of data.
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
