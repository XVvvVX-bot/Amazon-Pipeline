# Review Acquisition Pipeline

This repository currently has two tracks:

- A working Steam review ingestion testbed that stores cumulative review data in local Postgres.
- An app-store source evaluation track for deciding whether public Google Play or Apple App Store reviews can become the commercially stronger v1 source.

Steam remains useful for validating ingestion mechanics, but the project is now evaluating app-store review data before committing to a primary production source.

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

This development setup uses local Postgres as the durable database:

```bash
brew install postgresql@16
brew services start postgresql@16
/opt/homebrew/opt/postgresql@16/bin/createdb steam_reviews
/opt/homebrew/opt/postgresql@16/bin/createdb steam_reviews_test
.venv/bin/python steam_pipeline.py init-postgres --database-url postgresql:///steam_reviews
```

Skip the `createdb` commands if those databases already exist.

## Project Layout

- `steam_review_pipeline/`: target loading, API fetching, Postgres loading, validation, optional export, and daily orchestration.
- `steam_pipeline.py`: thin CLI wrapper for the Steam pipeline.
- `app_store_source_evaluation/`: conservative app-store source evaluation helpers.
- `app_store_evaluate.py`: CLI for summarizing app-store targets and running no-login storefront smoke tests.
- `data/targets/steam_apps.csv`: curated Steam app target list.
- `data/targets/app_store_public_apps.csv`: curated public cross-platform app target list for source evaluation.
- `data/evaluation/app_store_source_matrix.csv`: Google Play, Apple App Store, and licensed-provider feasibility matrix.
- `.github/workflows/steam-daily-pipeline.yml`: scheduled and manual Steam acquisition workflow.
- `.github/workflows/ci.yml`: test workflow for code changes.
- `docs/project_context.md`: current project direction and operating assumptions.
- `docs/app_store_source_evaluation.md`: app-store source evaluation memo, decision gate, and references.
- `docs/operations_runbook.md`: runbook for operating and troubleshooting the Steam pipeline.

## App Store Source Evaluation

The app-store evaluation is intentionally not a production ingestion pipeline yet. It exists to answer whether public Google Play or Apple App Store reviews can provide enough review depth, metadata, freshness, and operational stability for downstream analytics.

Summarize the public target set:

```bash
python app_store_evaluate.py targets --targets data/targets/app_store_public_apps.csv
```

Run a conservative no-login storefront smoke test:

```bash
python app_store_evaluate.py smoke \
  --targets data/targets/app_store_public_apps.csv \
  --limit 3 \
  --output /tmp/app_store_storefront_smoke.json
```

The smoke test fetches only public app detail pages. It does not use login state, personal cookies, hidden review endpoints, CAPTCHA solving, proxy rotation, or anti-bot bypasses.

See `docs/app_store_source_evaluation.md` before adding any app-store production code.

## Steam Target List

Steam targets live in `data/targets/steam_apps.csv` with these columns:

- `app_id`
- `app_name`
- `active`
- `notes`

Only rows with `active=true` are fetched.

## Run Locally

Run the normal incremental pipeline:

```bash
.venv/bin/python steam_pipeline.py daily
```

Default behavior:

- `review_filter=updated`
- `language=english`
- `purchase_type=all`
- `review_type=all`
- `num_per_page=100`
- `max_pages_per_app=0` (no page cap)
- `max_runtime_minutes=300`

Run a full public backfill against active targets:

```bash
.venv/bin/python steam_pipeline.py daily --review-filter recent --max-pages-per-app 0
```

Fetch only raw Steam review JSON pages:

```bash
.venv/bin/python steam_pipeline.py fetch --targets data/targets/steam_apps.csv --review-filter updated --max-pages-per-app 2
```

Load a fetched run into Postgres:

```bash
.venv/bin/python steam_pipeline.py load-postgres --raw-dir data/raw/steam/20260615T182053Z_9108e1
```

Validate the Steam database:

```bash
.venv/bin/python steam_pipeline.py validate-postgres
```

Export Steam reviews only when an analyst needs a file extract:

```bash
.venv/bin/python steam_pipeline.py export-postgres --format jsonl --output /tmp/steam_reviews.jsonl
```

## Outputs

- Sanitized raw JSON: `data/raw/steam/{run_id}/app_{app_id}_page_{page}.json`
- Page metadata: `data/raw/steam/{run_id}/review_pages.jsonl`
- Fetch report: `data/raw/steam/{run_id}/fetch_report.json`
- Durable database: local Postgres database `steam_reviews`
- Validation report: `data/reports/steam/{run_id}/validation_report.json`
- Daily report: `data/reports/steam/{run_id}/daily_report.json`

Raw JSON is sanitized before storage. The normalized Postgres tables do not store Steam user IDs; review identity is `recommendationid`.

## Steam Daily Automation

The scheduled workflow is `.github/workflows/steam-daily-pipeline.yml`. It runs on the local Mac self-hosted runner and writes to local Postgres by default with `postgresql:///steam_reviews`.

Workflow behavior:

1. Check that the local Postgres schema exists.
2. Run tests.
3. Fetch public Steam review pages for active app targets.
4. Save sanitized raw JSON and page metadata.
5. Upsert reviews into Postgres by `recommendationid`.
6. Validate the database and write reports.
7. Upload raw/report workflow artifacts for the run.

Manual workflow options:

- `full_backfill`: uses `filter=recent` and removes the page cap.
- `max_pages_per_app`: defaults to `0`, meaning no page cap; set a small value only for smoke tests.
- `max_runtime_minutes`: defaults to `300`; set to `0` for no runtime cap inside the job.

Daily runs use `filter=updated` and stop early for an app once fetched pages have caught up to that app's durable sync-state watermark. The watermark advances only after an app reaches a complete terminal reason such as `caught_up_to_existing_reviews`, `empty_page`, or `missing_next_cursor`. If an app stops because of a page cap, runtime cap, fetch error, or cursor issue, it is marked backlogged and the watermark does not advance.

## Postgres Schema

- `steam_runs`: one row per loaded Steam run.
- `steam_apps`: one row per app target.
- `steam_review_pages`: one row per fetched review-list API page.
- `steam_reviews`: one row per unique `recommendationid`, with full review text and Steam review metadata.
- `steam_review_changes`: one row per inserted or updated review seen in a run.
- `steam_app_sync_state`: one row per app with the durable updated-review watermark and backlog status.

Example review counts by app:

```sql
SELECT a.app_name, COUNT(*) AS review_count
FROM steam_reviews r
LEFT JOIN steam_apps a ON a.app_id = r.app_id
GROUP BY r.app_id, a.app_name
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
TEST_DATABASE_URL=postgresql:///steam_reviews_test .venv/bin/python -m pytest -q
```

## Migrating The Previous SQLite Snapshot

If you need to seed a fresh local Postgres database from the old release asset:

```bash
gh release download latest-steam-data --pattern steam_reviews.sqlite --dir /tmp/steam-pg-migration --clobber
.venv/bin/python steam_pipeline.py migrate-sqlite-to-postgres \
  --sqlite /tmp/steam-pg-migration/steam_reviews.sqlite \
  --database-url postgresql:///steam_reviews
```
