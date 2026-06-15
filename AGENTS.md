# Review Pipeline Agent Guide

Read this file before making changes in this repository.

## Project Objective

This project is a live review acquisition pipeline. The primary source is now Steam reviews because Steam exposes full written review rows through a documented public endpoint with cursor pagination. Amazon acquisition remains in the repository as a deprecated accessibility experiment and historical comparison.

## Ethical Boundaries

Keep all acquisition work inside these boundaries:

- Do not use login-required pages.
- Do not use personal Amazon cookies or browser sessions.
- Do not solve, bypass, or automate around CAPTCHA.
- Do not use proxy rotation or identity rotation.
- Do not use hidden APIs or anti-bot evasion.
- If Amazon returns a sign-in, robot-check, or blocked page, save the evidence, mark the target as blocked, and stop or slow down according to the configured safety rules.
- Do not store raw Steam user IDs in normalized SQLite tables or exports; Steam review identity is `recommendationid`.

## Current Architecture

The primary Steam pipeline is staged:

1. Read Steam app targets from `data/targets/steam_apps.csv`.
2. Fetch public Steam review JSON pages through `store.steampowered.com/appreviews/{app_id}`.
3. Save sanitized raw JSON pages and page metadata.
4. Normalize full written review rows.
5. Load normalized review rows into SQLite.
6. Export cumulative reviews to CSV.
7. Publish workflow artifacts and `latest-steam-data` release assets from GitHub Actions.

The deprecated Amazon pipeline is staged:

1. Discover product targets from public Amazon Best Sellers pages.
2. Store targets in `data/targets/amazon_products.csv`.
3. Fetch public product detail pages with Playwright rendered fetching for acquisition runs.
4. Save raw HTML and fetch metadata before parsing.
5. Parse visible top-review blocks from product pages.
6. Load normalized review rows into SQLite.
7. Export cumulative reviews to CSV.
8. Publish workflow artifacts and `latest-data` release assets from GitHub Actions.

The Steam workflow runs on `ubuntu-latest`. The deprecated Amazon workflow remains manual-only and runs on a self-hosted runner labeled `amazon-acquisition`.

## Core Commands

Run tests:

```bash
python -m pytest -q
```

Run daily Steam acquisition locally:

```bash
python steam_pipeline.py daily
```

Run deprecated Amazon acquisition locally with rendered fetching:

```bash
python amazon_pipeline.py daily --fetch-method playwright
```

Validate the Steam SQLite database:

```bash
python steam_pipeline.py validate --db data/steam_reviews.sqlite
```

Validate the deprecated Amazon SQLite database:

```bash
python amazon_pipeline.py validate --db data/reviews.sqlite
```

Export Steam reviews:

```bash
python steam_pipeline.py export --db data/steam_reviews.sqlite --format csv --output data/exports/steam_reviews.csv
```

Export deprecated Amazon reviews:

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
- `steam_pipeline.py`: CLI wrapper for the primary Steam pipeline.
- `steam_review_pipeline/`: Steam target loading, fetch, load, validation, export, and daily orchestration.
- `amazon_pipeline.py`: CLI wrapper for the staged pipeline.
- `amazon_review_pipeline/discovery.py`: Best Sellers discovery.
- `amazon_review_pipeline/fetcher.py`: product-page fetch logic.
- `amazon_review_pipeline/parser.py`: top-review parsing.
- `amazon_review_pipeline/database.py`: SQLite schema, loading, validation, and export.
- `amazon_review_pipeline/daily.py`: queue selection, batching, state updates, and daily reports.
- `.github/workflows/daily-pipeline.yml`: self-hosted acquisition automation.
- `docs/project_context.md`: project history and current open decisions.
- `docs/operations_runbook.md`: operating and troubleshooting guide.
