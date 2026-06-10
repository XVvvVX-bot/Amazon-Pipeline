# Project Context

## Current Requirement

John's current direction is to focus on the Amazon accessibility question. The project needs an evidence-based conclusion on whether Amazon is viable as a recurring live source under ethical constraints. A practical Amazon solution and a well-supported conclusion that Amazon is too restrictive are both acceptable outcomes.

The work should stay focused on live acquisition, not prepared/static datasets.

## Current Status

The project has moved beyond manual seed lists:

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

- Is Amazon stable enough for recurring live acquisition across multiple days?
- What block rate is acceptable for this project?
- Is top-review-only coverage sufficient for the first module?
- If deeper review coverage is required, can review pagination be accessed ethically and reliably?
- Should the next milestone focus on stronger monitoring, deeper coverage, or downstream analysis?

## Reports To Inspect After Each Run

Inspect these files from the workflow artifact or local `data/` folder:

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

## Evidence Needed For Amazon Viability

Before claiming Amazon is viable, collect evidence across at least one full stress run and at least one normal scheduled or incremental run:

- discovered product count,
- fetched product count,
- block/sign-in/CAPTCHA count,
- fetch error count,
- parsed review count,
- inserted review count,
- duplicate count,
- unresolved parse-error count,
- total runtime,
- whether the queue drained or hit a safety cap.

If Amazon fails, document the barrier clearly and keep saved raw pages/reports as evidence.
