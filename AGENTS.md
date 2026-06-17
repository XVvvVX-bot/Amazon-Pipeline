# Review Acquisition Pipeline Agent Guide

Read this file before making changes in this repository.

## Project Objective

This project is a live review acquisition and source-evaluation project. The working ingestion testbed collects public, structured Steam review JSON from `store.steampowered.com/appreviews/{app_id}`, normalizes full written review rows, stores them in Postgres, and publishes per-run raw/report artifacts through GitHub Actions.

The current source-selection work evaluates whether public Google Play or Apple App Store reviews can become the commercially stronger v1 source. Do not add a production `app_store_review_pipeline` until the app-store source passes the documented feasibility gate in `docs/app_store_source_evaluation.md`.

## Ethical Boundaries

Keep acquisition work inside these boundaries:

- Use documented public endpoints only.
- Do not use login-required pages.
- Do not use personal cookies, browser sessions, or account state.
- Do not solve, bypass, or automate around access controls.
- Do not use proxy rotation or identity rotation.
- Do not use hidden APIs or anti-bot evasion.
- For app-store source evaluation, use only public app detail pages, official owner APIs, or licensed/documented provider APIs. Treat scraper-only libraries and undocumented storefront endpoints as research leads, not production paths.
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

App-store source evaluation is separate from this production-style Steam workflow:

1. Read public cross-platform app targets from `data/targets/app_store_public_apps.csv`.
2. Maintain the feasibility matrix in `data/evaluation/app_store_source_matrix.csv`.
3. Run no-login public storefront smoke tests only for accessibility evidence.
4. Compare official owner APIs, public storefronts, and licensed providers before writing production ingestion code.

## Core Commands

Run tests:

```bash
TEST_DATABASE_URL=postgresql:///steam_reviews_test .venv/bin/python -m pytest -q
```

Summarize app-store public targets:

```bash
.venv/bin/python app_store_evaluate.py targets
```

Run a conservative app-store storefront smoke test:

```bash
.venv/bin/python app_store_evaluate.py smoke --limit 3 --output /tmp/app_store_storefront_smoke.json
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
- Inspect `docs/app_store_source_evaluation.md` before changing source strategy.
- Preserve the staged pipeline boundaries: target selection, fetch, load, validate, report.
- Keep live network calls out of the test suite; use fixtures and fake sessions.
- Do not introduce a reviewer/user table. The review is the primary unit of data.
- Do not advance `steam_app_sync_state.complete_through_timestamp_updated` after an incomplete stop such as a page cap, runtime cap, fetch error, or cursor issue.
- Avoid hardcoded local machine paths in code or workflows.
- Do not commit large raw JSON, database, export, or artifact outputs unless explicitly requested.
- Prefer improving reports, observability, and reproducibility before adding downstream modeling.

## Key Files

- `README.md`: command reference and architecture overview.
- `app_store_evaluate.py`: app-store source evaluation CLI.
- `app_store_source_evaluation/`: app-store target loading and storefront smoke helpers.
- `steam_pipeline.py`: CLI wrapper.
- `steam_review_pipeline/`: Steam target loading, fetch, load, validation, export, and daily orchestration.
- `data/targets/steam_apps.csv`: curated target list.
- `data/targets/app_store_public_apps.csv`: curated public app-store source evaluation targets.
- `data/evaluation/app_store_source_matrix.csv`: app-store source feasibility matrix.
- `.github/workflows/steam-daily-pipeline.yml`: scheduled/manual acquisition automation.
- `.github/workflows/ci.yml`: test workflow.
- `docs/project_context.md`: current project direction and open decisions.
- `docs/app_store_source_evaluation.md`: app-store source decision memo and gate.
- `docs/operations_runbook.md`: operating and troubleshooting guide.
