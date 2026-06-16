# Operations Runbook

## Fresh Clone Orientation

Start by reading:

- `AGENTS.md`
- `README.md`
- `docs/project_context.md`
- this runbook

Then inspect repository health:

```bash
TEST_DATABASE_URL=postgresql:///steam_reviews_test .venv/bin/python -m pytest -q
```

The repository should not require machine-specific absolute paths. The scheduled acquisition workflow runs on the local Mac self-hosted runner because the development database is local Postgres.

## Persistent Source Of Truth

Small persistent targets live in Git:

- `data/targets/steam_apps.csv`

Routine cumulative data now lives in local Postgres:

- database URL: `postgresql:///steam_reviews`
- database name: `steam_reviews`

The old `latest-steam-data` release is only a migration source for the first local Postgres seed.

Large raw JSON, reports, exports, databases, and downloaded artifacts should not usually be committed to Git.

## Normal Daily Workflow

GitHub Actions runs `.github/workflows/steam-daily-pipeline.yml` on the local Mac self-hosted runner.

Normal daily behavior:

1. Check or initialize the local Postgres schema.
2. Run tests.
3. Fetch public Steam review pages for active app targets.
4. Sanitize raw JSON before storage.
5. Upsert reviews into Postgres by `recommendationid`.
6. Validate and write daily reports.
7. Upload raw/report workflow artifacts.

Current scheduled defaults:

- 20 curated Steam apps.
- `filter=updated`.
- `language=english`.
- `purchase_type=all`.
- `review_type=all`.
- `num_per_page=100`.
- `max_pages_per_app=0`, meaning no page cap.
- `max_runtime_minutes=300`.
- 1 second between apps.
- Delta stop is enabled for `filter=updated`; an app stops early once fetched pages have caught up to the app's durable sync-state watermark.
- The sync-state watermark advances only after a complete terminal reason: `caught_up_to_existing_reviews`, `empty_page`, or `missing_next_cursor`.
- If an app stops because of `page_cap_reached`, `runtime_limit_reached`, `fetch_error`, or `cursor_not_advancing`, it is marked backlogged and the watermark does not advance.

## Manual Workflow Modes

Open GitHub Actions, choose **Daily Steam Review Pipeline**, then **Run workflow**.

`full_backfill`:

- Uses `filter=recent`.
- Sets `max_pages_per_app=0`, meaning no page cap.
- Use only for deliberate backfill runs.

`max_pages_per_app`:

- Defaults to `0`, meaning no page cap.
- Set to a small number such as `2` for smoke tests.
- Set to `0` for no cap.

`max_runtime_minutes`:

- Defaults to `300`.
- Set lower for smoke tests.
- Set to `0` only for deliberate uncapped manual recovery.

## Interpreting Reports

Steam report fields:

- `fetch_summary.page_count`: review API pages fetched or attempted.
- `fetch_summary.reviews_seen`: review rows observed before DB idempotency checks.
- `fetch_summary.fetch_errors`: failed pages after retries.
- `fetch_summary.rate_limited_pages`: pages that ended with HTTP 429.
- `fetch_summary.capped_apps`: apps that hit `max_pages_per_app`; these should be treated as backlogged.
- `load_summary.reviews_inserted`: new recommendation IDs inserted.
- `load_summary.reviews_updated`: existing recommendation IDs updated because Steam changed the review.
- `load_summary.duplicates_skipped`: unchanged recommendation IDs already present.
- `sync_state_summary.complete_apps`: apps whose completeness watermark advanced or remained safely complete.
- `sync_state_summary.backlogged_apps`: apps that stopped incompletely and need a later catch-up run.
- `validation_report.quality`: missing text/language and duplicate checks.

## Recovery Steps

Local Postgres unavailable:

- Check service status with `brew services list`.
- Start it with `brew services start postgresql@16`.
- Confirm connectivity with `/opt/homebrew/opt/postgresql@16/bin/pg_isready -d steam_reviews`.
- Re-run `.venv/bin/python steam_pipeline.py init-postgres`.

Self-hosted runner unavailable:

- Confirm the runner is online in GitHub repository settings.
- On the Mac, check `/Users/xvvvvx/github-runners/amazon-pipeline/svc.sh status`.
- Start the service if needed before the next scheduled run.

Steam fetch errors or rate limits:

- Inspect `data/raw/steam/{run_id}/fetch_report.json`.
- Check `fetch_summary.fetch_errors`, `rate_limited_pages`, and page-level `error_message`.
- Add more delay or lower `max_runtime_minutes` if the job needs a smaller operational window.

Unexpectedly low review counts:

- Inspect per-app entries in `validation_report.json`.
- Check `steam_review_pages` for empty pages, fetch errors, or cap hits.
- Run a small manual smoke test with `max_pages_per_app=2` before rerunning a broad backfill.

Duplicate-heavy run:

- This is normal when an app catches up to its sync-state watermark because the boundary page overlaps already-known reviews.
- Check `reviews_inserted`, `reviews_updated`, and `duplicates_skipped` together.

Backlogged app:

- Check `sync_state_summary.backlogged_apps` and `validation_report.sync_state`.
- A later normal run will retry because the app watermark did not advance.
- For manual recovery, run with `max_pages_per_app=0` and enough `max_runtime_minutes` to reach `caught_up_to_existing_reviews` or `empty_page`.

Large artifact or release uploads:

- Backfills can generate several GB of unpacked raw JSON locally.
- Prefer scheduled incremental mode for routine operation.
- Use Postgres as the analyst-facing source of truth; export files only for ad hoc handoff.

## Local Inspection Commands

Count Postgres tables:

```bash
/opt/homebrew/opt/postgresql@16/bin/psql -d steam_reviews -c "
SELECT 'apps' AS table_name, COUNT(*) FROM steam_apps
UNION ALL SELECT 'pages', COUNT(*) FROM steam_review_pages
UNION ALL SELECT 'reviews', COUNT(*) FROM steam_reviews
UNION ALL SELECT 'runs', COUNT(*) FROM steam_runs;"
```

Review counts by app:

```bash
/opt/homebrew/opt/postgresql@16/bin/psql -d steam_reviews -c "
SELECT a.app_name, COUNT(*) AS reviews
FROM steam_reviews r
LEFT JOIN steam_apps a ON a.app_id = r.app_id
GROUP BY r.app_id, a.app_name
ORDER BY reviews DESC;"
```

Latest run report:

```bash
find data/reports/steam -name daily_report.json -print | sort | tail -1
```

Seed Postgres from the old SQLite release:

```bash
gh release download latest-steam-data --pattern steam_reviews.sqlite --dir /tmp/steam-pg-migration --clobber
.venv/bin/python steam_pipeline.py migrate-sqlite-to-postgres \
  --sqlite /tmp/steam-pg-migration/steam_reviews.sqlite \
  --database-url postgresql:///steam_reviews
```
