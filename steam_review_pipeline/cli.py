from __future__ import annotations

import argparse
import json
from pathlib import Path

from steam_review_pipeline.config import (
    DEFAULT_DB_PATH,
    DEFAULT_EXPORT_CSV,
    DEFAULT_LANGUAGE,
    DEFAULT_NUM_PER_PAGE,
    DEFAULT_PURCHASE_TYPE,
    DEFAULT_RAW_ROOT,
    DEFAULT_REPORTS_ROOT,
    DEFAULT_REVIEW_TYPE,
    DEFAULT_TARGETS,
    VALID_REVIEW_FILTERS,
)
from steam_review_pipeline.daily import run_daily_pipeline
from steam_review_pipeline.database import export_reviews, load_pipeline_run, validate_database
from steam_review_pipeline.fetcher import fetch_apps
from steam_review_pipeline.files import write_json, write_jsonl
from steam_review_pipeline.targets import load_targets
from amazon_review_pipeline.utils import make_run_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Staged Steam full-review fetching pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch raw Steam review JSON pages.")
    add_fetch_arguments(fetch)
    fetch.set_defaults(func=command_fetch)

    load = subparsers.add_parser("load", help="Load Steam raw review pages into SQLite.")
    load.add_argument("--raw-dir", type=Path, required=True)
    load.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    load.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    load.set_defaults(func=command_load)

    validate = subparsers.add_parser("validate", help="Validate the Steam SQLite database.")
    validate.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    validate.add_argument("--run-id")
    validate.add_argument("--output", type=Path)
    validate.set_defaults(func=command_validate)

    export = subparsers.add_parser("export", help="Export loaded Steam reviews.")
    export.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    export.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--run-id")
    export.set_defaults(func=command_export)

    daily = subparsers.add_parser("daily", help="Fetch, load, validate, export, and report Steam reviews.")
    add_fetch_arguments(daily)
    daily.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    daily.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    daily.add_argument("--export-csv", type=Path, default=DEFAULT_EXPORT_CSV)
    daily.set_defaults(func=command_daily)

    return parser


def add_fetch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--review-filter", choices=sorted(VALID_REVIEW_FILTERS), default="updated")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--purchase-type", default=DEFAULT_PURCHASE_TYPE)
    parser.add_argument("--review-type", default=DEFAULT_REVIEW_TYPE)
    parser.add_argument("--num-per-page", type=int, default=DEFAULT_NUM_PER_PAGE)
    parser.add_argument("--max-pages-per-app", type=int, default=50, help="0 means no page cap.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--request-delay-seconds", type=float, default=0.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=float, default=5.0)


def command_fetch(args: argparse.Namespace) -> int:
    apps = [app for app in load_targets(args.targets) if app.active]
    run_id = make_run_id()
    raw_dir = args.raw_root / run_id
    report = fetch_apps(
        apps,
        raw_dir,
        review_filter=args.review_filter,
        language=args.language,
        purchase_type=args.purchase_type,
        review_type=args.review_type,
        num_per_page=args.num_per_page,
        max_pages_per_app=args.max_pages_per_app,
        timeout=args.timeout,
        request_delay_seconds=args.request_delay_seconds,
        max_attempts=args.max_attempts,
        retry_delay_seconds=args.retry_delay_seconds,
    )
    for row in report["page_reports"]:
        row["run_id"] = run_id
    write_jsonl(raw_dir / "review_pages.jsonl", report["page_reports"])
    write_json(raw_dir / "fetch_report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def command_load(args: argparse.Namespace) -> int:
    summary = load_pipeline_run(args.db, args.raw_dir, args.targets)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    report = validate_database(args.db, args.run_id)
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


def command_export(args: argparse.Namespace) -> int:
    summary = export_reviews(args.db, args.output, args.format, args.run_id)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def command_daily(args: argparse.Namespace) -> int:
    report = run_daily_pipeline(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
