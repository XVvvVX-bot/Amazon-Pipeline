# Amazon Pipeline Agent Guide

Read this file before making changes in this repository.

## Project Objective

This project is a live Amazon review acquisition pipeline. The goal is not to analyze a prepared or static Amazon dataset. The goal is to test whether Amazon can work as a recurring public web source for automated review ingestion, incremental updates, monitoring, and downstream analysis.

## Ethical Boundaries

Keep all acquisition work inside these boundaries:

- Do not use login-required pages.
- Do not use personal Amazon cookies or browser sessions.
- Do not solve, bypass, or automate around CAPTCHA.
- Do not use proxy rotation or identity rotation.
- Do not use hidden APIs or anti-bot evasion.
- If Amazon returns a sign-in, robot-check, or blocked page, save the evidence, mark the target as blocked, and stop or slow down according to the configured safety rules.

## Current Architecture

The pipeline is staged:

1. Discover product targets from public Amazon Best Sellers pages.
2. Store targets in `data/targets/amazon_products.csv`.
3. Fetch public product detail pages with Playwright rendered fetching for acquisition runs.
4. Save raw HTML and fetch metadata before parsing.
5. Parse visible top-review blocks from product pages.
6. Load normalized review rows into SQLite.
7. Export cumulative reviews to CSV.
8. Publish workflow artifacts and `latest-data` release assets from GitHub Actions.

The acquisition workflow runs on a self-hosted runner labeled `amazon-acquisition`. The runner is currently intended to move from a Windows machine to a MacBook, and later should remain portable to a company VM.

## Core Commands

Run tests:

```bash
python -m pytest -q
```

Run daily acquisition locally with rendered fetching:

```bash
python amazon_pipeline.py daily --fetch-method playwright
```

Validate the SQLite database:

```bash
python amazon_pipeline.py validate --db data/reviews.sqlite
```

Export reviews:

```bash
python amazon_pipeline.py export --db data/reviews.sqlite --format csv --output data/exports/reviews.csv
```

## Rules For Future Agents

- Inspect recent reports before changing acquisition strategy.
- Preserve the staged pipeline boundaries: discovery, fetch, parse, load, validate, export.
- Keep `reviews.jsonl` optional; do not make it required for loading.
- Do not reintroduce a `reviewers` table. The review is the primary unit of data.
- Avoid hardcoded local machine paths in code or workflows.
- Keep workflow changes compatible with macOS/Linux self-hosted runners.
- Do not commit large raw HTML, database, export, or artifact outputs unless explicitly requested.
- Prefer improving reports and reproducibility before adding downstream modeling.

## Key Files

- `README.md`: command reference and architecture overview.
- `amazon_pipeline.py`: CLI wrapper for the staged pipeline.
- `amazon_review_pipeline/discovery.py`: Best Sellers discovery.
- `amazon_review_pipeline/fetcher.py`: product-page fetch logic.
- `amazon_review_pipeline/parser.py`: top-review parsing.
- `amazon_review_pipeline/database.py`: SQLite schema, loading, validation, and export.
- `amazon_review_pipeline/daily.py`: queue selection, batching, state updates, and daily reports.
- `.github/workflows/daily-pipeline.yml`: self-hosted acquisition automation.
- `docs/project_context.md`: project history and current open decisions.
- `docs/operations_runbook.md`: operating and troubleshooting guide.
