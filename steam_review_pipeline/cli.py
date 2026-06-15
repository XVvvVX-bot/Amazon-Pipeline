from __future__ import annotations

import argparse
import json
from pathlib import Path

from steam_review_pipeline.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_DB_PATH,
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
from steam_review_pipeline.fetcher import fetch_apps
from steam_review_pipeline.files import write_json, write_jsonl
from steam_review_pipeline.postgres_database import (
    export_reviews_postgres,
    initialize_postgres,
    mask_database_url,
    migrate_sqlite_to_postgres,
    validate_postgres,
)
from steam_review_pipeline.targets import load_targets
from steam_review_pipeline.utils import make_run_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Staged Steam full-review fetching pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch raw Steam review JSON pages.")
    add_fetch_arguments(fetch)
    fetch.set_defaults(func=command_fetch)

    init_postgres = subparsers.add_parser("init-postgres", help="Create or update the Postgres schema.")
    init_postgres.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    init_postgres.set_defaults(func=command_init_postgres)

    load = subparsers.add_parser("load", aliases=["load-postgres"], help="Load Steam raw review pages into Postgres.")
    load.add_argument("--raw-dir", type=Path, required=True)
    load.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    load.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    load.set_defaults(func=command_load_postgres)

    validate = subparsers.add_parser("validate", aliases=["validate-postgres"], help="Validate the Steam Postgres database.")
    validate.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    validate.add_argument("--run-id")
    validate.add_argument("--output", type=Path)
    validate.set_defaults(func=command_validate_postgres)

    export = subparsers.add_parser("export", aliases=["export-postgres"], help="Export loaded Steam reviews from Postgres.")
    export.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    export.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--run-id")
    export.set_defaults(func=command_export_postgres)

    migrate = subparsers.add_parser("migrate-sqlite-to-postgres", help="Import an existing Steam SQLite database into Postgres.")
    migrate.add_argument("--sqlite", type=Path, default=DEFAULT_DB_PATH)
    migrate.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    migrate.add_argument("--batch-size", type=int, default=5000)
    migrate.set_defaults(func=command_migrate_sqlite_to_postgres)

    daily = subparsers.add_parser("daily", help="Fetch, load into Postgres, validate, and report Steam reviews.")
    add_fetch_arguments(daily)
    daily.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    daily.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    daily.add_argument("--disable-delta-stop", action="store_true", help="Fetch to the page cap even when filter=updated has caught up.")
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


def command_init_postgres(args: argparse.Namespace) -> int:
    initialize_postgres(args.database_url)
    print(json.dumps({"database_url": mask_database_url(args.database_url), "initialized": True}, indent=2, sort_keys=True))
    return 0


def command_load_postgres(args: argparse.Namespace) -> int:
    from steam_review_pipeline.postgres_database import load_pipeline_run_postgres

    summary = load_pipeline_run_postgres(args.database_url, args.raw_dir, args.targets)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def command_validate_postgres(args: argparse.Namespace) -> int:
    report = validate_postgres(args.database_url, args.run_id)
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


def command_export_postgres(args: argparse.Namespace) -> int:
    summary = export_reviews_postgres(args.database_url, args.output, args.format, args.run_id)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def command_migrate_sqlite_to_postgres(args: argparse.Namespace) -> int:
    summary = migrate_sqlite_to_postgres(args.sqlite, args.database_url, args.batch_size)
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
