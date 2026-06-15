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

The MacBook or future VM should decide how to install local dependencies. The repository should not require machine-specific absolute paths.

## Persistent Source Of Truth

Small persistent Steam targets live in Git:

- `data/targets/steam_apps.csv`

Deprecated Amazon state also lives in Git:

- `data/targets/amazon_products.csv`
- `data/state/pipeline_state.json`

Cumulative Steam data is published through the GitHub `latest-steam-data` release:

- `steam_reviews.sqlite`
- `steam_reviews.csv`

Deprecated Amazon data is published through the GitHub `latest-data` release:

- `reviews.sqlite`
- `reviews.csv`

Large raw/sanitized JSON or HTML, parsed reports, discovery outputs, and run reports are workflow artifacts. They are useful for inspection but should not usually be committed to Git.

## Normal Daily Workflow

GitHub Actions runs `.github/workflows/steam-daily-pipeline.yml` on `ubuntu-latest`.

Normal daily behavior:

1. Download latest cumulative `steam_reviews.sqlite` and `steam_reviews.csv` if available.
2. Run tests.
3. Fetch public Steam review pages for active app targets.
4. Sanitize raw JSON by removing Steam user IDs before storage.
5. Load, validate, and export.
7. Upload artifacts.
8. Update the `latest-steam-data` release.

Current scheduled defaults:

- 20 curated Steam apps.
- `filter=updated`.
- `language=english`.
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

Deprecated Amazon modes live under **Manual Amazon Review Pipeline (Deprecated)**:

`retry_recent_blocked`:

- Retries targets still inside the blocked/CAPTCHA cooldown.
- Use this only to test whether a new acquisition strategy recovers previously blocked targets.

`refetch_everything`:

- Makes every active target due immediately.
- Useful for full refresh tests.
- Expect a much longer run than a normal incremental daily run.

`aggressive_stress_test`:

- Removes discovery caps.
- Recurses through Best Sellers department/subdepartment pages until terminal pages.
- Follows visible Best Sellers pagination.
- Forces all active targets due.
- Uses faster pacing and still keeps runtime/block-rate safety stops.
- Use this for one-off accessibility and scale testing, not routine daily automation.

## Interpreting Daily Reports

Steam report fields:

- `fetch_summary.page_count`: review API pages fetched or attempted.
- `fetch_summary.reviews_seen`: review rows observed before DB idempotency checks.
- `fetch_summary.fetch_errors`: failed pages after retries.
- `fetch_summary.rate_limited_pages`: pages that ended with HTTP 429.
- `fetch_summary.capped_apps`: apps that hit `max_pages_per_app`.
- `load_summary.reviews_inserted`: new recommendation IDs inserted.
- `load_summary.reviews_updated`: existing recommendation IDs updated because Steam changed the review.
- `load_summary.duplicates_skipped`: unchanged recommendation IDs already present.
- `validation_report.quality`: missing text/language checks.

Deprecated Amazon report fields:

`fetch_summary`:

- `fetched`: successful product-page fetches.
- `blocked`: blocked/sign-in/robot-check pages.
- `fetch_errors`: network or fetch failures.
- `review_sections_detected`: fetched pages where review containers were visible.
- `raw_html_deduplicated`: fetched content matched a previously stored raw page.

`load_summary`:

- `reviews_seen`: parsed reviews before idempotency checks.
- `reviews_inserted`: new rows inserted.
- `duplicates_skipped`: reviews already present in SQLite.
- `parse_errors_recorded`: target-level parse/fetch issues recorded.

`validation_report`:

- `counts`: cumulative or scoped table counts.
- `quality`: missing review fields and duplicate IDs.
- `rating_distribution`: rating spread.
- `date_coverage`: review date coverage.
- `parse_error_summary`: historical and currently unresolved parsing issues.

## Recovery Steps

Failed `latest-steam-data` release download:

- If the step logs "No previous steam_reviews.sqlite release asset found; continuing", the run can start from an empty local database.
- If GitHub API errors repeat and fail the step, rerun later or inspect GitHub release availability.

Steam fetch errors or rate limits:

- Inspect `data/raw/steam/{run_id}/fetch_report.json`.
- Check `fetch_summary.fetch_errors`, `rate_limited_pages`, and page-level `error_message`.
- Lower `max_pages_per_app` or add more delay before increasing scope.

Deprecated Amazon runner offline:

- Check GitHub **Settings > Actions > Runners**.
- Ensure exactly one intended acquisition machine has the `amazon-acquisition` label.
- Start the runner service/process on that machine.

No products discovered:

- Inspect `discovery_report.json`.
- Check `blocked_pages`, `page_reports`, and saved discovery HTML.
- Do not expand to arbitrary Amazon links; stay inside Best Sellers navigation.

High block rate:

- Inspect blocked raw HTML and `blocked_reasons`.
- Slow pacing before increasing scope.
- Do not introduce cookies, CAPTCHA solving, proxies, or bypass behavior.

No reviews parsed:

- Inspect `review_sections_detected` in fetch metadata.
- Open the saved raw HTML for affected targets.
- Confirm whether the page truly lacks visible review containers or whether parser selectors need updating.

Duplicate-heavy run:

- This is normal for full refetches.
- Check `reviews_inserted` and `duplicates_skipped` together.
- The database is idempotent by Amazon review ID when available, otherwise by stable review content hash.
