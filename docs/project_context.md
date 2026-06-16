# Project Context

## Current Direction

The project is a recurring live Steam review pipeline. The goal is to collect many full text reviews per app from a public, structured source, then make the cumulative dataset available for downstream analysis through Postgres.

The work should stay focused on live acquisition, not prepared/static datasets.

## Current Status

- `data/targets/steam_apps.csv` seeds 20 high-volume Steam apps.
- The pipeline fetches public review JSON from `store.steampowered.com/appreviews/{app_id}`.
- Steam review pagination uses cursors with `num_per_page=100`.
- Daily runs use `filter=updated`; manual backfills can use `filter=recent`.
- Raw JSON pages are sanitized before storage.
- Postgres stores apps, review pages, runs, review changes, and full written review rows keyed by `recommendationid`.
- Per-app sync state stores a durable `complete_through_timestamp_updated` watermark and backlog status.
- The scheduled workflow is `.github/workflows/steam-daily-pipeline.yml` on the local Mac self-hosted runner.
- Routine cumulative data lives in local Postgres at `postgresql:///steam_reviews`.
- CSV is no longer part of the daily workflow; exports are ad hoc.

## Recent Evidence

The first manual full backfill completed successfully on June 15, 2026:

- 20 target apps.
- 11,070 review API pages.
- 1,104,820 review rows inserted.
- 0 fetch errors.
- 0 rate-limited pages.
- `steam_reviews.sqlite` and `steam_reviews.csv` were published to `latest-steam-data`.

The local Postgres development database was seeded from that release on June 15, 2026:

- 20 apps imported.
- 11,070 review pages imported.
- 1,104,820 reviews imported.

A one-app Postgres smoke run for app `730` then completed successfully:

- 1 page fetched.
- 99 reviews seen.
- 15 reviews inserted.
- 4 reviews updated.
- 80 duplicates skipped.

## Open Questions

- How many Steam pages per app should the normal scheduled run fetch after the first backfill?
- How quickly does `filter=updated` refresh old `recommendationid` rows?
- Should target discovery expand beyond the curated 20-app seed list?
- Should discovery be an advisory workflow that produces candidate apps without mutating `data/targets/steam_apps.csv`?
- When the project is ready for production, should Postgres move from the Mac to managed cloud Postgres?
- After the first uncapped `updated` run establishes sync state, what normal runtime cap gives the best completeness/runtime balance?

## Reports To Inspect After Each Run

Inspect these files from the workflow artifact or local `data/` folder:

- `data/reports/steam/{run_id}/daily_report.json`
- `data/reports/steam/{run_id}/validation_report.json`
- `data/raw/steam/{run_id}/fetch_report.json`
- `data/raw/steam/{run_id}/review_pages.jsonl`

The most important fields are:

- `fetch_summary.page_count`
- `fetch_summary.reviews_seen`
- `fetch_summary.fetch_errors`
- `fetch_summary.rate_limited_pages`
- `fetch_summary.capped_apps`
- `load_summary.reviews_inserted`
- `load_summary.reviews_updated`
- `load_summary.duplicates_skipped`
- `sync_state_summary.complete_apps`
- `sync_state_summary.backlogged_apps`
- `validation_report.quality`

## Evidence Needed For Source Viability

Before expanding scope, collect evidence across at least one full backfill and one normal scheduled incremental run:

- target app count,
- fetched page count,
- fetch error/rate-limit count,
- inserted and updated review count,
- duplicate count,
- missing review text/language counts,
- total runtime,
- whether page caps were reached.

If Steam fails to provide sufficient review volume or reliability, document the barrier clearly and keep saved sanitized raw pages/reports as evidence.
