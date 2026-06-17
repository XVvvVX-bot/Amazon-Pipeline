# Project Context

## Current Direction

The project is a recurring live review acquisition project. Steam is now the ingestion mechanics testbed; the next source-selection decision is whether public Google Play or Apple App Store reviews can provide stronger commercial value and broader customer-feedback coverage.

The work should stay focused on live acquisition, not prepared/static datasets.

## Current Status

- `docs/app_store_source_evaluation.md` documents the app-store source decision gate.
- `data/targets/app_store_public_apps.csv` contains 20 mainstream public app targets across both Google Play and Apple App Store.
- `data/evaluation/app_store_source_matrix.csv` compares official owner APIs, public storefront pages, and licensed-provider paths.
- `app_store_evaluate.py` can summarize targets and run conservative no-login storefront smoke tests.
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
- The Steam workflow schedule has been reverted to a daily baseline while app-store source evaluation proceeds.

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

- Can public Google Play or Apple App Store storefronts expose enough full-review depth without hidden endpoints, login state, CAPTCHA solving, proxies, or brittle bypass behavior?
- If public storefronts do not pass, which licensed provider has the best combination of review history, metadata, terms, API ergonomics, and cost?
- Should v1 production source be licensed public app-store reviews, official owner APIs for partner apps, or another mainstream review source?
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

For app-store source selection, collect evidence before writing production ingestion code:

- public-third-party support,
- full written review text availability,
- stable review IDs or reliable deterministic dedupe keys,
- rating, date, version, country/locale, and developer-response metadata,
- pagination or batching behavior,
- daily incremental refresh method,
- expected historical depth for popular apps,
- access terms, pricing, and operational risk,
- fit with the existing Postgres cumulative-storage architecture.

For Steam pipeline mechanics, collect evidence across at least one full backfill and one normal scheduled incremental run:

- target app count,
- fetched page count,
- fetch error/rate-limit count,
- inserted and updated review count,
- duplicate count,
- missing review text/language counts,
- total runtime,
- whether page caps were reached.

If Steam fails to provide sufficient review volume or reliability, document the barrier clearly and keep saved sanitized raw pages/reports as evidence.
