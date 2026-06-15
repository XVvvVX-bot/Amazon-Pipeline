# Review Acquisition Pipeline

This repository now uses Steam as the primary live review source because Steam exposes public, cursor-paginated full review text through `store.steampowered.com/appreviews/{app_id}`. The previous Amazon top-review pipeline remains as a deprecated manual experiment for historical comparison.

## Install

```bash
python -m pip install -r requirements.txt
```

Playwright is only needed for the deprecated Amazon workflow:

```bash
python -m playwright install chromium
```

## Project Layout

- `steam_review_pipeline/`: primary Steam review pipeline package.
- `steam_pipeline.py`: thin CLI wrapper for the Steam pipeline.
- `data/targets/steam_apps.csv`: curated Steam app targets.
- `.github/workflows/steam-daily-pipeline.yml`: scheduled Steam acquisition workflow.
- `amazon_review_pipeline/config.py`: shared paths, CSV schema, request headers, and block markers.
- `amazon_review_pipeline/models.py`: dataclasses used across the pipeline.
- `amazon_review_pipeline/targets.py`: target CSV loading, boolean parsing, and ASIN inference.
- `amazon_review_pipeline/fetcher.py`: product-page fetching and blocked/sign-in detection.
- `amazon_review_pipeline/parser.py`: top-review HTML parsing and field normalization.
- `amazon_review_pipeline/files.py`: JSONL writing, raw-run lookup, and `latest` directory handling.
- `amazon_review_pipeline/database.py`: SQLite schema, idempotent loading, and validation reports.
- `amazon_review_pipeline/daily.py`: daily discovery, queue selection, batch fetching, state updates, and exports.
- `amazon_review_pipeline/commands.py`: fetch, parse, and run workflow orchestration.
- `amazon_review_pipeline/cli.py`: command-line argument parsing for `amazon_pipeline.py`.
- `amazon_review_pipeline/discovery.py`: Amazon Best Sellers discovery and target CSV merging.
- `amazon_pipeline.py` and `amazon_bestsellers.py`: deprecated Amazon compatibility wrappers.

## Target List

Primary Steam targets live in `data/targets/steam_apps.csv` with these columns:

- `app_id`
- `app_name`
- `active`
- `notes`

Deprecated Amazon targets live in `data/targets/amazon_products.csv` with these columns:

- `target_id`
- `url`
- `asin`
- `product_name`
- `category`
- `active`
- `notes`

## Run The Steam Pipeline

Run the daily Steam pipeline locally:

```bash
python steam_pipeline.py daily
```

By default this uses:

- `review_filter=updated` for daily incremental collection.
- `language=english`.
- `purchase_type=all`.
- `review_type=all`.
- `num_per_page=100`.
- `max_pages_per_app=50`.

Run an initial public backfill against active Steam targets:

```bash
python steam_pipeline.py daily --review-filter recent --max-pages-per-app 0
```

Fetch only raw Steam review JSON pages:

```bash
python steam_pipeline.py fetch --targets data/targets/steam_apps.csv --review-filter updated --max-pages-per-app 2
```

Load a fetched Steam run into SQLite:

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

Steam outputs:

- Sanitized raw JSON: `data/raw/steam/{run_id}/app_{app_id}_page_{page}.json`
- Page metadata: `data/raw/steam/{run_id}/review_pages.jsonl`
- Fetch report: `data/raw/steam/{run_id}/fetch_report.json`
- SQLite database: `data/steam_reviews.sqlite`
- CSV export: `data/exports/steam_reviews.csv`
- Validation report: `data/reports/steam/{run_id}/validation_report.json`
- Daily report: `data/reports/steam/{run_id}/daily_report.json`

Steam raw JSON is sanitized before storage to remove raw Steam user IDs. Normalized review identity uses `recommendationid`.

## Deprecated Amazon Pipeline

Discover Amazon Best Sellers category pages automatically, extract product URLs, and merge unique ASIN targets into the target list:

```bash
python amazon_bestsellers.py --targets data/targets/amazon_products.csv
```

Run a smaller controlled discovery smoke test:

```bash
python amazon_bestsellers.py --max-seed-pages 2 --max-products-per-page 10 --delay 0 --targets data/targets/amazon_products.csv
```

Fetch raw HTML from active targets:

```bash
python amazon_pipeline.py fetch --targets data/targets/amazon_products.csv
```

Fetch modes:

- `requests`: fast static HTTP fetch.
- `playwright`: clean Chromium render, no login cookies.
- `auto`: try `requests`, then retry with Playwright only when the page is not blocked but no review section is detected.

```bash
python amazon_pipeline.py fetch --targets data/targets/amazon_products.csv --fetch-method auto
```

By default, fetch reuses a prior successful raw page for the same `target_id` instead of requesting Amazon again. To force a fresh network request:

```bash
python amazon_pipeline.py fetch --targets data/targets/amazon_products.csv --force
```

Forced fetches still avoid writing duplicate raw HTML when the fetched content hash already exists; metadata points to the existing raw file instead.

Equivalent package-style command:

```bash
python -m amazon_review_pipeline fetch --targets data/targets/amazon_products.csv
```

Parse saved raw HTML without another network request. By default this writes `parse_report.json` only and does not keep the intermediate review JSONL:

```bash
python amazon_pipeline.py parse --raw-dir data/raw/latest
```

Keep the optional review JSONL staging file:

```bash
python amazon_pipeline.py parse --raw-dir data/raw/latest --keep-jsonl
```

Fetch and parse in one command:

```bash
python amazon_pipeline.py run --targets data/targets/amazon_products.csv
```

Load parsed reviews and raw metadata into SQLite. If `reviews.jsonl` is absent, the loader parses the saved raw HTML directly:

```bash
python amazon_pipeline.py load --parsed-dir data/parsed/20260608T193901Z_4a170c --raw-dir data/raw/20260608T193901Z_4a170c --db data/reviews.sqlite
```

Validate the loaded database:

```bash
python amazon_pipeline.py validate --db data/reviews.sqlite --run-id 20260608T193901Z_4a170c --output data/parsed/20260608T193901Z_4a170c/validation_report.json
```

Export loaded reviews for downstream analysis:

```bash
python amazon_pipeline.py export --db data/reviews.sqlite --format csv --output data/exports/reviews.csv
python amazon_pipeline.py export --db data/reviews.sqlite --format jsonl --output data/exports/reviews.jsonl
```

Export only one loaded run:

```bash
python amazon_pipeline.py export --db data/reviews.sqlite --run-id 20260608T223058Z_2aaa75 --format csv --output data/exports/reviews_20260608T223058Z_2aaa75.csv
```

Run the deprecated Amazon daily pipeline manually:

```bash
python amazon_pipeline.py daily
```

The Amazon daily command discovers new Best Sellers ASINs, updates `data/targets/amazon_products.csv`, builds an incremental fetch queue, fetches due targets in controlled batches, parses and loads each batch, validates the database, exports `data/exports/reviews.csv`, and updates `data/state/pipeline_state.json`.

For self-hosted Amazon acquisition, prefer rendered fetching with randomized pacing:

```bash
python amazon_pipeline.py daily --fetch-method playwright --batch-size 45 --target-delay-min-seconds 5 --target-delay-max-seconds 12 --batch-cooldown-min-minutes 3 --batch-cooldown-max-minutes 6 --max-runtime-minutes 180 --max-block-rate 0.20
```

Outputs:

- Raw HTML: `data/raw/{run_id}/{target_id}.html`
- Fetch metadata: `data/raw/{run_id}/fetch_metadata.jsonl`
- Latest raw snapshot: `data/raw/latest/`
- Parsed reviews JSONL, optional: `data/parsed/{run_id}/reviews.jsonl`
- Parse report: `data/parsed/{run_id}/parse_report.json`
- SQLite database: `data/reviews.sqlite`
- Validation report: `data/parsed/{run_id}/validation_report.json`
- Review exports: `data/exports/*.csv` or `data/exports/*.jsonl`
- Discovery seed pages: `data/discovery/{run_id}/seed_pages.jsonl`
- Discovery products: `data/discovery/{run_id}/bestseller_products.jsonl`
- Discovery report: `data/discovery/{run_id}/discovery_report.json`
- Daily state: `data/state/pipeline_state.json`
- Daily progress checkpoint: `data/reports/{run_id}/daily_progress.json`
- Daily report: `data/reports/{run_id}/daily_report.json`

The script does not log in, solve CAPTCHA, use proxies, rotate identities, or bypass access controls. If Amazon returns a sign-in or robot-check page, the response is still saved for inspection and the metadata marks it as `blocked_or_signin`.

`amazon_bestsellers.py` starts from the public Best Sellers root page, discovers Best Sellers department and subdepartment pages, follows visible Best Sellers pagination links, and writes target IDs as `amzn_{asin}` so duplicate product names from different sellers do not collide. Use `--max-seed-pages`, `--max-departments`, `--max-subdepartment-depth`, `--max-subdepartment-pages`, `--max-pages-per-seed`, `--max-products-per-page`, and `--delay` to keep discovery bounded. A value of `0` removes that specific discovery cap.

## Daily Automation

The scheduled workflow is `.github/workflows/steam-daily-pipeline.yml`. It runs on `ubuntu-latest`, downloads prior cumulative Steam outputs from the `latest-steam-data` release, runs tests, fetches Steam review pages, loads/validates/exports SQLite and CSV outputs, uploads artifacts, and updates the release.

Manual Steam workflow options:

- `full_backfill`: uses `filter=recent` and removes the page cap.
- `max_pages_per_app`: defaults to `50`; set to `0` for no cap.

Deprecated Amazon automation is manual-only in `.github/workflows/daily-pipeline.yml`.

The deprecated Amazon automation uses an incremental queue instead of manually choosing a fetch range.

Fetch queue rules:

- Fetch newly discovered ASINs.
- Fetch active ASINs that have never been fetched.
- Refresh stale ASINs after 7 days.
- Retry network failures after 1 day.
- Skip blocked/sign-in targets for 3 days.
- Manual workflow runs can override the blocked cooldown for an accessibility experiment.
- Manual workflow runs can also force a full refetch of every active target.

Batch behavior:

- CLI default batch size: 50 targets when no workflow override is supplied.
- Default fixed cooldown between batches: 10 minutes when no cooldown range is supplied.
- Self-hosted acquisition should use randomized per-target delays and randomized batch cooldowns instead of a fixed cadence.
- The workflow currently uses 45 targets per batch, 5-12 seconds between targets, and 3-6 minutes between batches.
- If a batch starts showing CAPTCHA blocks, the next cooldown is multiplied by the adaptive slowdown settings.
- Workflow runtime cap: 180 minutes.
- Stop early if block rate reaches 20% or 5 targets are blocked consecutively.

GitHub Actions:

- `.github/workflows/ci.yml` runs tests on GitHub-hosted runners for code changes.
- `.github/workflows/steam-daily-pipeline.yml` runs daily on GitHub-hosted `ubuntu-latest`.
- `.github/workflows/daily-pipeline.yml` is the manual-only deprecated Amazon workflow on a self-hosted runner labeled `amazon-acquisition`.
- Manual daily runs include a `retry_recent_blocked` option. Set it to `true` only when testing whether the current Playwright and randomized pacing strategy can recover targets that were previously blocked.
- Manual daily runs also include a `refetch_everything` option. Set it to `true` only when testing full-refresh behavior, because it makes every active target due immediately.
- Manual daily runs include an `aggressive_stress_test` option. Set it to `true` only for a one-off stress test; it removes discovery caps, recursively follows Best Sellers subdepartments to terminal pages, follows visible pagination links, and uses faster full-refetch pacing.
- The daily workflow uses `--fetch-method playwright` because GitHub-hosted/static HTTP fetching produced frequent blocks or pages without parsable review DOM.
- It downloads the previous `reviews.sqlite` and `reviews.csv` from the `latest-data` GitHub release if available.
- It uploads raw HTML, parsed reports, discovery outputs, daily reports, and exports as workflow artifacts.
- It updates the `latest-data` release with the latest cumulative `reviews.sqlite` and `reviews.csv`.
- It commits only small persistent files back to Git: `data/targets/amazon_products.csv` and `data/state/pipeline_state.json`.

Blocked-target experiment:

1. Open **Actions > Daily Amazon Review Pipeline > Run workflow**.
2. Select `main`.
3. Enable `retry_recent_blocked`.
4. Run the workflow.
5. Compare the daily report's `queue.due_targets`, `fetch_summary.blocked`, `fetch_summary.fetched`, and `pacing.cooldowns` against the previous run.

If the blocked retry succeeds with low block rate, the randomized/adaptive strategy is likely helping. If it quickly blocks again, Amazon remains restrictive even under slower self-hosted acquisition.

Full-refetch experiment:

1. Open **Actions > Daily Amazon Review Pipeline > Run workflow**.
2. Select `main`.
3. Enable `refetch_everything`.
4. Run the workflow.
5. Compare the daily report's `queue.due_targets`, `batch_reports`, `fetch_summary.blocked`, `load_summary.duplicates_skipped`, and `pacing.cooldowns`.

This mode sets stale-days and blocked-cooldown-days to zero for that manual run, so it tests whether the current pacing can handle the entire active target list. It still keeps the normal safety stops for block rate, consecutive blocked pages, and max runtime.

Aggressive stress test:

1. Open **Actions > Daily Amazon Review Pipeline > Run workflow**.
2. Select `main`.
3. Enable `aggressive_stress_test`.
4. Run the workflow from the self-hosted acquisition runner.
5. Inspect `discovery_report.json` for department/subdepartment/page counts before judging the product fetch results.

This mode sets discovery caps to zero, forces all active targets due, uses 50 targets per batch, uses 1-4 seconds between targets, and uses 1-3 minutes between batches. It still stops cleanly on max runtime, high block rate, or too many consecutive blocked targets.

### Self-Hosted Acquisition Runner

Use a personal machine as the first acquisition runner, then move the same workflow to a company VM later.

Runner requirements:

- GitHub self-hosted runner registered for this repository.
- Runner label: `amazon-acquisition`.
- Python 3.12.
- GitHub CLI `gh` authenticated enough to read/create/upload the `latest-data` release.
- macOS or Linux shell environment for workflow script steps.
- Python dependencies from `requirements.txt`.
- Playwright Chromium installed with `python -m playwright install chromium`.

Personal-machine setup:

1. In GitHub, open **Settings > Actions > Runners > New self-hosted runner**.
2. Install the runner on the acquisition machine using GitHub's commands.
3. Add the custom label `amazon-acquisition`.
4. Start the runner as a background service or keep the runner process open during scheduled acquisition.
5. Manually run **Daily Amazon Review Pipeline** from the Actions tab.

Company-VM migration:

1. Provision the VM and install the same prerequisites.
2. Register a new self-hosted runner with the same `amazon-acquisition` label.
3. Stop or remove the personal-machine runner.
4. Re-run the same GitHub workflow without code changes.

The acquisition runner still uses a clean browser context. Do not copy personal Amazon cookies, login sessions, CAPTCHA tokens, proxy credentials, or browser profiles into the runner.

## Steam SQLite Schema

- `steam_runs`: one row per loaded Steam run.
- `steam_apps`: one row per app target.
- `steam_review_pages`: one row per fetched review-list API page.
- `steam_reviews`: one row per unique `recommendationid`, with full review text and Steam review metadata.

Steam does not store raw Steam user IDs in normalized SQLite tables or exports.

## Deprecated Amazon SQLite Schema

- `ingestion_runs`: one row per loaded run, keyed by `run_id`.
- `products`: one row per product target, keyed by `target_id`.
- `raw_pages`: one row per target fetched or reused during a run. Connects to `ingestion_runs` by `run_id` and to `products` by `target_id`.
- `reviews`: one row per unique review, keyed by `review_key`. Connects to `ingestion_runs` by `run_id` and to `products` by `target_id`.
- `parse_errors`: fetch or parse issues by `run_id` and `target_id`.

Review identity uses Amazon `review_id` when available. If no `review_id` is present, the pipeline creates a stable content hash from review fields.

## Example SQL Queries

Review counts by product:

```sql
SELECT target_id, COUNT(*) AS review_count
FROM reviews
GROUP BY target_id
ORDER BY review_count DESC;
```

Rating distribution:

```sql
SELECT rating, COUNT(*) AS review_count
FROM reviews
GROUP BY rating
ORDER BY rating;
```

Review date coverage:

```sql
SELECT MIN(review_date_iso) AS earliest_review, MAX(review_date_iso) AS latest_review
FROM reviews
WHERE review_date_iso IS NOT NULL;
```

Parse issues from one run:

```sql
SELECT target_id, error_type, message
FROM parse_errors
WHERE run_id = '20260608T223058Z_2aaa75'
ORDER BY target_id;
```
