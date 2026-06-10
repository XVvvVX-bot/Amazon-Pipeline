from __future__ import annotations

import argparse
from pathlib import Path

from amazon_review_pipeline.commands import command_daily, command_export, command_fetch, command_load, command_parse, command_run, command_validate
from amazon_review_pipeline.config import DEFAULT_DB_PATH, DEFAULT_PARSED_ROOT, DEFAULT_RAW_ROOT, DEFAULT_TARGETS
from amazon_review_pipeline.daily import DEFAULT_REPORTS_ROOT, DEFAULT_STATE_PATH
from amazon_review_pipeline.discovery import BESTSELLERS_URL, DEFAULT_DISCOVERY_ROOT
from amazon_review_pipeline.fetcher import FETCH_METHODS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Staged Amazon top-review fetching pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch raw Amazon product HTML from a target CSV.")
    fetch.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    fetch.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    fetch.add_argument("--timeout", type=float, default=20.0)
    fetch.add_argument("--fetch-method", choices=sorted(FETCH_METHODS), default="auto")
    fetch.add_argument("--force", action="store_true", help="Fetch from the network even when a reusable raw page already exists.")
    fetch.set_defaults(func=command_fetch)

    parse = subparsers.add_parser("parse", help="Parse saved raw HTML into review artifacts.")
    parse.add_argument("--raw-dir", type=Path, required=True)
    parse.add_argument("--parsed-root", type=Path, default=DEFAULT_PARSED_ROOT)
    parse.add_argument("--keep-jsonl", action="store_true", help="Write reviews.jsonl staging output. Defaults to false.")
    parse.set_defaults(func=command_parse)

    run = subparsers.add_parser("run", help="Fetch and then parse the latest raw HTML.")
    run.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    run.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    run.add_argument("--parsed-root", type=Path, default=DEFAULT_PARSED_ROOT)
    run.add_argument("--timeout", type=float, default=20.0)
    run.add_argument("--fetch-method", choices=sorted(FETCH_METHODS), default="auto")
    run.add_argument("--keep-jsonl", action="store_true", help="Write reviews.jsonl staging output during parse. Defaults to false.")
    run.add_argument("--force", action="store_true", help="Fetch from the network even when a reusable raw page already exists.")
    run.set_defaults(func=command_run)

    load = subparsers.add_parser("load", help="Load parsed reviews and raw metadata into SQLite.")
    load.add_argument("--parsed-dir", type=Path, required=True)
    load.add_argument("--raw-dir", type=Path, required=True)
    load.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    load.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    load.set_defaults(func=command_load)

    validate = subparsers.add_parser("validate", help="Validate loaded SQLite review data.")
    validate.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    validate.add_argument("--run-id")
    validate.add_argument("--output", type=Path)
    validate.set_defaults(func=command_validate)

    export = subparsers.add_parser("export", help="Export loaded reviews from SQLite to CSV or JSONL.")
    export.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    export.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--run-id")
    export.set_defaults(func=command_export)

    daily = subparsers.add_parser("daily", help="Run automated discovery and batch-until-drained incremental ingestion.")
    daily.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    daily.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    daily.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    daily.add_argument("--parsed-root", type=Path, default=DEFAULT_PARSED_ROOT)
    daily.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    daily.add_argument("--discovery-root", type=Path, default=DEFAULT_DISCOVERY_ROOT)
    daily.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    daily.add_argument("--export-csv", type=Path, default=Path("data/exports/reviews.csv"))
    daily.add_argument("--seed-url", default=BESTSELLERS_URL)
    daily.add_argument("--max-seed-pages", type=int, default=12)
    daily.add_argument("--max-departments", type=int, default=12)
    daily.add_argument("--max-subdepartment-depth", type=int, default=1)
    daily.add_argument("--max-subdepartment-pages", type=int, default=25)
    daily.add_argument("--max-pages-per-seed", type=int, default=1)
    daily.add_argument("--max-products-per-page", type=int, default=0)
    daily.add_argument("--discovery-delay", type=float, default=2.0)
    daily.add_argument("--timeout", type=float, default=20.0)
    daily.add_argument("--fetch-method", choices=sorted(FETCH_METHODS), default="auto")
    daily.add_argument("--batch-size", type=int, default=50)
    daily.add_argument("--batch-cooldown-minutes", type=float, default=10.0, help="Fixed cooldown between batches when no cooldown range is supplied.")
    daily.add_argument("--batch-cooldown-min-minutes", type=float, help="Minimum randomized cooldown between batches.")
    daily.add_argument("--batch-cooldown-max-minutes", type=float, help="Maximum randomized cooldown between batches.")
    daily.add_argument("--target-delay-seconds", type=float, default=0.0, help="Fixed delay between product targets when no target-delay range is supplied.")
    daily.add_argument("--target-delay-min-seconds", type=float, help="Minimum randomized delay between product targets.")
    daily.add_argument("--target-delay-max-seconds", type=float, help="Maximum randomized delay between product targets.")
    daily.add_argument("--stale-days", type=int, default=7)
    daily.add_argument("--blocked-cooldown-days", type=int, default=3)
    daily.add_argument("--error-retry-days", type=int, default=1)
    daily.add_argument("--max-runtime-minutes", type=float, default=300.0)
    daily.add_argument("--max-block-rate", type=float, default=0.25)
    daily.add_argument("--max-consecutive-blocked", type=int, default=5)
    daily.add_argument("--adaptive-slow-block-rate", type=float, default=0.05)
    daily.add_argument("--adaptive-strong-slow-block-rate", type=float, default=0.15)
    daily.add_argument("--adaptive-slowdown-multiplier", type=float, default=2.0)
    daily.add_argument("--adaptive-strong-slowdown-multiplier", type=float, default=3.0)
    daily.set_defaults(func=command_daily)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
