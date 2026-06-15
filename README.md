# Steam Review Pipeline

This repository runs a live Steam review acquisition pipeline. Steam exposes public, cursor-paginated full review text through `store.steampowered.com/appreviews/{app_id}`, so the pipeline can collect structured review rows without browser automation or login-gated pages.

## Install

```bash
python -m pip install -r requirements.txt
```

## Project Layout

- `steam_review_pipeline/`: target loading, API fetching, SQLite loading, validation, export, and daily orchestration.
- `steam_pipeline.py`: thin CLI wrapper for the Steam pipeline.
- `data/targets/steam_apps.csv`: curated Steam app target list.
- `.github/workflows/steam-daily-pipeline.yml`: scheduled and manual Steam acquisition workflow.
- `.github/workflows/ci.yml`: test workflow for code changes.
- `docs/project_context.md`: current project direction and operating assumptions.
- `docs/operations_runbook.md`: runbook for operating and troubleshooting the Steam pipeline.

## Target List

Steam targets live in `data/targets/steam_apps.csv` with these columns:

- `app_id`
- `app_name`
- `active`
- `notes`

Only rows with `active=true` are fetched.

## Run Locally

Run the normal incremental pipeline:

```bash
python steam_pipeline.py daily
```

Default behavior:

- `review_filter=updated`
- `language=english`
- `purchase_type=all`
- `review_type=all`
- `num_per_page=100`
- `max_pages_per_app=50`

Run a full public backfill against active targets:

```bash
python steam_pipeline.py daily --review-filter recent --max-pages-per-app 0
```

Fetch only raw Steam review JSON pages:

```bash
python steam_pipeline.py fetch --targets data/targets/steam_apps.csv --review-filter updated --max-pages-per-app 2
```

Load a fetched run into SQLite:

```bash
python steam_pipeline.py load --raw-dir data/raw/steam/20260615T182053Z_9108e1 --db data/steam_reviews.sqlite
```

Validate the Steam database:

```bash
python steam_pipeline.py validate --db data/steam_reviews.sqlite
```

Export Steam reviews:

```bash
python steam_pipeline.py export --db data/steam_reviews.sqlite --format csv --output data/exports/steam_reviews.csv
```

## Outputs

- Sanitized raw JSON: `data/raw/steam/{run_id}/app_{app_id}_page_{page}.json`
- Page metadata: `data/raw/steam/{run_id}/review_pages.jsonl`
- Fetch report: `data/raw/steam/{run_id}/fetch_report.json`
- SQLite database: `data/steam_reviews.sqlite`
- CSV export: `data/exports/steam_reviews.csv`
- Validation report: `data/reports/steam/{run_id}/validation_report.json`
- Daily report: `data/reports/steam/{run_id}/daily_report.json`

Raw JSON is sanitized before storage. The normalized SQLite database and CSV export do not store Steam user IDs; review identity is `recommendationid`.

## Daily Automation

The scheduled workflow is `.github/workflows/steam-daily-pipeline.yml`. It runs on GitHub-hosted `ubuntu-latest`.

Workflow behavior:

1. Download prior cumulative `steam_reviews.sqlite` and `steam_reviews.csv` from the `latest-steam-data` release if available.
2. Run tests.
3. Fetch public Steam review pages for active app targets.
4. Save sanitized raw JSON and page metadata.
5. Load, validate, and export SQLite/CSV outputs.
6. Upload workflow artifacts.
7. Update the `latest-steam-data` release.

Manual workflow options:

- `full_backfill`: uses `filter=recent` and removes the page cap.
- `max_pages_per_app`: defaults to `50`; set to `0` for no cap.

## SQLite Schema

- `steam_runs`: one row per loaded Steam run.
- `steam_apps`: one row per app target.
- `steam_review_pages`: one row per fetched review-list API page.
- `steam_reviews`: one row per unique `recommendationid`, with full review text and Steam review metadata.

Example review counts by app:

```sql
SELECT a.app_name, COUNT(*) AS review_count
FROM steam_reviews r
LEFT JOIN steam_apps a ON a.app_id = r.app_id
GROUP BY r.app_id
ORDER BY review_count DESC;
```

Recommendation distribution:

```sql
SELECT voted_up, COUNT(*) AS review_count
FROM steam_reviews
GROUP BY voted_up
ORDER BY voted_up DESC;
```

Recent updated reviews:

```sql
SELECT app_id, recommendationid, updated_at_iso, voted_up, review
FROM steam_reviews
ORDER BY timestamp_updated DESC
LIMIT 25;
```

## Checks

```bash
git diff --check
python -m pytest -q
```
