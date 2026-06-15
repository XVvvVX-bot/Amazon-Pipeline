# Operations Runbook

## Fresh Clone Orientation

Start by reading:

- `AGENTS.md`
- `README.md`
- `docs/project_context.md`
- this runbook

Then inspect repository health:

```bash
python -m pytest -q
```

The repository should not require machine-specific absolute paths. The scheduled pipeline runs on GitHub-hosted `ubuntu-latest`.

## Persistent Source Of Truth

Small persistent targets live in Git:

- `data/targets/steam_apps.csv`

Cumulative data is published through the GitHub `latest-steam-data` release:

- `steam_reviews.sqlite`
- `steam_reviews.csv`

Large raw JSON, reports, exports, databases, and downloaded artifacts should not usually be committed to Git.

## Normal Daily Workflow

GitHub Actions runs `.github/workflows/steam-daily-pipeline.yml` on `ubuntu-latest`.

Normal daily behavior:

1. Download latest cumulative `steam_reviews.sqlite` and `steam_reviews.csv` if available.
2. Run tests.
3. Fetch public Steam review pages for active app targets.
4. Sanitize raw JSON before storage.
5. Load, validate, and export.
6. Upload workflow artifacts.
7. Update the `latest-steam-data` release.

Current scheduled defaults:

- 20 curated Steam apps.
- `filter=updated`.
- `language=english`.
- `purchase_type=all`.
- `review_type=all`.
- `num_per_page=100`.
- `max_pages_per_app=50`.
- 1 second between apps.

## Manual Workflow Modes

Open GitHub Actions, choose **Daily Steam Review Pipeline**, then **Run workflow**.

`full_backfill`:

- Uses `filter=recent`.
- Sets `max_pages_per_app=0`, meaning no page cap.
- Use only for deliberate backfill runs.

`max_pages_per_app`:

- Defaults to `50`.
- Set to a small number such as `2` for smoke tests.
- Set to `0` for no cap.

## Interpreting Reports

Steam report fields:

- `fetch_summary.page_count`: review API pages fetched or attempted.
- `fetch_summary.reviews_seen`: review rows observed before DB idempotency checks.
- `fetch_summary.fetch_errors`: failed pages after retries.
- `fetch_summary.rate_limited_pages`: pages that ended with HTTP 429.
- `fetch_summary.capped_apps`: apps that hit `max_pages_per_app`.
- `load_summary.reviews_inserted`: new recommendation IDs inserted.
- `load_summary.reviews_updated`: existing recommendation IDs updated because Steam changed the review.
- `load_summary.duplicates_skipped`: unchanged recommendation IDs already present.
- `validation_report.quality`: missing text/language and duplicate checks.

## Recovery Steps

Failed `latest-steam-data` release download:

- If the step logs `No previous steam_reviews.sqlite release asset found; continuing`, the run can start from an empty local database.
- If GitHub API errors repeat and fail the step, rerun later or inspect GitHub release availability.

Steam fetch errors or rate limits:

- Inspect `data/raw/steam/{run_id}/fetch_report.json`.
- Check `fetch_summary.fetch_errors`, `rate_limited_pages`, and page-level `error_message`.
- Lower `max_pages_per_app` or add more delay before increasing scope.

Unexpectedly low review counts:

- Inspect per-app entries in `validation_report.json`.
- Check `steam_review_pages` for empty pages, fetch errors, or cap hits.
- Run a small manual smoke test with `max_pages_per_app=2` before rerunning a broad backfill.

Duplicate-heavy run:

- This is normal after a full backfill when daily `filter=updated` revisits known reviews.
- Check `reviews_inserted`, `reviews_updated`, and `duplicates_skipped` together.

Large artifact or release uploads:

- Backfills can generate hundreds of MB in SQLite/CSV assets and several GB when raw JSON is unpacked locally.
- Prefer scheduled incremental mode for routine operation.
- Use the `latest-steam-data` release for cumulative analyst-facing assets.

## Local Inspection Commands

Count SQLite tables:

```bash
sqlite3 data/steam_reviews.sqlite \
  "SELECT 'apps', COUNT(*) FROM steam_apps
   UNION ALL SELECT 'pages', COUNT(*) FROM steam_review_pages
   UNION ALL SELECT 'reviews', COUNT(*) FROM steam_reviews
   UNION ALL SELECT 'runs', COUNT(*) FROM steam_runs;"
```

Review counts by app:

```bash
sqlite3 data/steam_reviews.sqlite \
  "SELECT a.app_name, COUNT(*) AS reviews
   FROM steam_reviews r
   LEFT JOIN steam_apps a ON a.app_id = r.app_id
   GROUP BY r.app_id
   ORDER BY reviews DESC;"
```

Latest run report:

```bash
find data/reports/steam -name daily_report.json -print | sort | tail -1
```
