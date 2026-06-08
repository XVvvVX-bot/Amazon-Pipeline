from __future__ import annotations

import argparse
from pathlib import Path

from amazon_review_pipeline.commands import command_fetch, command_load, command_parse, command_run, command_validate
from amazon_review_pipeline.config import DEFAULT_DB_PATH, DEFAULT_PARSED_ROOT, DEFAULT_RAW_ROOT, DEFAULT_TARGETS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Staged Amazon top-review fetching pipeline.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="Fetch raw Amazon product HTML from a target CSV.")
    fetch.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    fetch.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    fetch.add_argument("--timeout", type=float, default=20.0)
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
