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

Small persistent state lives in Git:

- `data/targets/amazon_products.csv`
- `data/state/pipeline_state.json`

Cumulative data is published through the GitHub `latest-data` release:

- `reviews.sqlite`
- `reviews.csv`

Large raw HTML, parsed reports, discovery outputs, and run reports are workflow artifacts. They are useful for inspection but should not usually be committed to Git.

## Normal Daily Workflow

GitHub Actions runs `.github/workflows/daily-pipeline.yml` on the self-hosted runner labeled `amazon-acquisition`.

Normal daily behavior:

1. Download latest cumulative `reviews.sqlite` and `reviews.csv` if available.
2. Run tests.
3. Run Best Sellers discovery with conservative bounds.
4. Build the due-fetch queue.
5. Fetch due products with Playwright.
6. Parse, load, validate, and export.
7. Upload artifacts.
8. Update the `latest-data` release.
9. Commit updated target/state files if they changed.

## Manual Workflow Modes

Open GitHub Actions, choose **Daily Amazon Review Pipeline**, then **Run workflow**.

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

`stop_reason`:

- `queue_drained`: all due targets were processed.
- `max_runtime_reached`: the safety runtime cap stopped the run. This is not necessarily a failure.
- `max_block_rate_reached`: Amazon access became too restricted for the configured run.
- `max_consecutive_blocked_reached`: repeated blocked pages caused an early stop.

`queue`:

- `due_targets`: targets selected for this run.
- `batches_planned`: expected batches.
- `batches_completed`: completed batches.
- `remaining_targets`: due targets not processed before a safety stop.

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

Failed latest-data release download:

- If the step logs "No previous reviews.sqlite release asset found; continuing", the run can start from an empty local database.
- If GitHub API errors repeat and fail the step, rerun later or inspect GitHub release availability.

Runner offline:

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
