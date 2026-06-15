# Project Context

## Current Requirement

John's current direction is to pivot away from Amazon because complete full-review coverage is not available through a public, legal, and ethical Amazon path. The project should now prove a recurring live review pipeline against a source that exposes many full text reviews per item.

The work should stay focused on live acquisition, not prepared/static datasets.

## Current Status

Steam is now the primary source:

- `data/targets/steam_apps.csv` seeds 20 high-volume Steam apps.
- The Steam pipeline fetches public review JSON from `store.steampowered.com/appreviews/{app_id}`.
- Steam review pagination uses cursors with `num_per_page=100`.
- Daily runs use `filter=updated`; initial/manual backfills can use `filter=recent`.
- Raw JSON pages are sanitized before storage to remove raw Steam user IDs.
- SQLite stores apps, review pages, runs, and full written review rows keyed by `recommendationid`.
- The scheduled workflow is `.github/workflows/steam-daily-pipeline.yml` on `ubuntu-latest`.
- Cumulative Steam data is published to the `latest-steam-data` release.

Amazon remains as a deprecated experiment:

- Best Sellers discovery starts from the public Amazon Best Sellers root page.
- Discovery can expand through department and subdepartment Best Sellers navigation.
- Product targets are deduplicated by ASIN and stored as `amzn_{asin_lowercase}` target IDs.
- The daily pipeline builds a due-fetch queue from new, never-fetched, stale, and retryable targets.
- Acquisition uses a self-hosted GitHub Actions runner instead of GitHub-hosted `ubuntu-latest`.
- Product-page acquisition uses Playwright rendered fetching in a clean browser context.
- The parser extracts visible top-review blocks from product detail pages.
- SQLite stores products, raw pages, ingestion runs, reviews, and parse errors.
- CSV export and validation reports are generated after acquisition.
- The workflow publishes artifacts and updates the `latest-data` release.
- The Amazon workflow is manual-only and preserved for historical comparison.

## Why Self-Hosted Acquisition Exists

GitHub-hosted runners produced unreliable Amazon access, including blocked pages and pages without the expected review DOM. A self-hosted runner gives a more realistic production acquisition environment while keeping the process observable through GitHub Actions logs and artifacts.

The self-hosted runner must still use a clean browser context. It must not use personal cookies, login sessions, proxies, CAPTCHA solving, or anti-bot bypass behavior.

## Known Limitation

The current parser captures visible top reviews from product pages, usually around 8-10 reviews per product. This supports broad product coverage, but it does not collect full review history for each product.

The next product decision is whether to prioritize:

- more product coverage with top reviews only,
- deeper review coverage per product,
- or a hybrid strategy.

## Current Open Questions

- How many Steam pages per app should the normal scheduled run fetch?
- How quickly does `filter=updated` refresh old recommendation IDs?
- Should target discovery expand beyond the curated 20-app seed list?
- When should the deprecated Amazon workflow be archived entirely?

## Reports To Inspect After Each Run

For Steam, inspect these files from the workflow artifact or local `data/` folder:

- `data/reports/steam/{run_id}/daily_report.json`
- `data/reports/steam/{run_id}/validation_report.json`
- `data/raw/steam/{run_id}/fetch_report.json`
- `data/raw/steam/{run_id}/review_pages.jsonl`
- `data/steam_reviews.sqlite`
- `data/exports/steam_reviews.csv`

The most important fields are:

- `fetch_summary.page_count`
- `fetch_summary.reviews_seen`
- `fetch_summary.fetch_errors`
- `fetch_summary.rate_limited_pages`
- `fetch_summary.capped_apps`
- `load_summary.reviews_inserted`
- `load_summary.reviews_updated`
- `load_summary.duplicates_skipped`
- `export_summary.review_count`
- `validation_report.quality`

For deprecated Amazon runs, inspect:

- `data/reports/{run_id}/daily_report.json`
- `data/discovery/{run_id}/discovery_report.json`
- `data/reports/{run_id}/validation_report.json`
- `data/reviews.sqlite`
- `data/exports/reviews.csv`

The most important fields are:

- `stop_reason`
- `queue.due_targets`
- `queue.remaining_targets`
- `discovery_report.discovered_products`
- `discovery_report.merge_summary.added_targets`
- `batch_reports[*].fetch_summary`
- `batch_reports[*].parse_summary.review_count`
- `batch_reports[*].load_summary.reviews_inserted`
- `batch_reports[*].load_summary.duplicates_skipped`
- `export_summary.review_count`
- `validation_report.parse_error_summary`

## Evidence Needed For Source Viability

Before claiming Steam is viable, collect evidence across at least one manual backfill-style smoke run and at least one normal scheduled incremental run:

- target app count,
- fetched page count,
- fetch error/rate-limit count,
- parsed review count,
- inserted and updated review count,
- duplicate count,
- unresolved parse-error count,
- total runtime,
- whether page caps were reached.

If Steam fails to provide sufficient review volume or reliability, document the barrier clearly and keep saved sanitized raw pages/reports as evidence.
