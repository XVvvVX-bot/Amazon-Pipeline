from __future__ import annotations

import argparse
import json
from pathlib import Path

from amazon_review_pipeline.database import export_reviews, load_pipeline_run, validate_database
from amazon_review_pipeline.extraction import extract_reviews_from_raw_dir
from amazon_review_pipeline.fetch_cache import build_content_hash_index, find_reusable_fetch, reuse_fetch_metadata
from amazon_review_pipeline.fetcher import fetch_target
from amazon_review_pipeline.files import (
    infer_run_id,
    load_fetch_metadata,
    resolve_raw_dir,
    update_latest_dir,
    write_jsonl,
    write_reviews,
)
from amazon_review_pipeline.models import Target
from amazon_review_pipeline.targets import load_targets
from amazon_review_pipeline.utils import make_run_id


def command_fetch(args: argparse.Namespace) -> int:
    targets = load_targets(args.targets)
    active_targets = [target for target in targets if target.active]
    run_id = make_run_id()
    run_raw_dir = args.raw_root / run_id
    metadata_path = run_raw_dir / "fetch_metadata.jsonl"
    run_raw_dir.mkdir(parents=True, exist_ok=True)
    force_fetch = bool(getattr(args, "force", False))
    content_hash_index = build_content_hash_index(args.raw_root, exclude_dir=run_raw_dir)

    metadata_rows = []
    for target in active_targets:
        reusable_metadata = None if force_fetch else find_reusable_fetch(args.raw_root, target, exclude_dir=run_raw_dir)
        if reusable_metadata:
            metadata = reuse_fetch_metadata(target, reusable_metadata)
        else:
            metadata = fetch_target(target, run_raw_dir, args.timeout, content_hash_index)
        metadata["run_id"] = run_id
        metadata_rows.append(metadata)

    write_jsonl(metadata_rows, metadata_path)
    update_latest_dir(run_raw_dir, args.raw_root / "latest")
    print(json.dumps(fetch_summary(run_id, args.targets, targets, metadata_rows, metadata_path), indent=2, sort_keys=True))
    return 0


def command_parse(args: argparse.Namespace) -> int:
    raw_dir = resolve_raw_dir(args.raw_dir)
    metadata_by_target = load_fetch_metadata(raw_dir / "fetch_metadata.jsonl")
    run_id = infer_run_id(raw_dir, metadata_by_target)

    output_dir = args.parsed_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    reviews_path = output_dir / "reviews.jsonl"
    report_path = output_dir / "parse_report.json"
    keep_jsonl = bool(getattr(args, "keep_jsonl", False))

    all_reviews, target_reports = extract_reviews_from_raw_dir(raw_dir, metadata_by_target)

    if keep_jsonl:
        write_reviews(all_reviews, reviews_path)
        reviews_path_value = str(reviews_path)
    else:
        if reviews_path.exists():
            reviews_path.unlink()
        reviews_path_value = None
    report = {
        "raw_dir": str(raw_dir),
        "keep_jsonl": keep_jsonl,
        "reviews_path": reviews_path_value,
        "target_count": len(target_reports),
        "review_count": len(all_reviews),
        "targets": target_reports,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def command_run(args: argparse.Namespace) -> int:
    fetch_args = argparse.Namespace(targets=args.targets, raw_root=args.raw_root, timeout=args.timeout, force=args.force)
    command_fetch(fetch_args)
    parse_args = argparse.Namespace(raw_dir=args.raw_root / "latest", parsed_root=args.parsed_root, keep_jsonl=args.keep_jsonl)
    return command_parse(parse_args)


def command_load(args: argparse.Namespace) -> int:
    summary = load_pipeline_run(args.db, args.parsed_dir, args.raw_dir, args.targets)
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


def fetch_summary(run_id: str, targets_path: Path, targets: list[Target], metadata_rows: list[dict], metadata_path: Path) -> dict:
    return {
        "run_id": run_id,
        "targets_path": str(targets_path),
        "active_targets": sum(1 for target in targets if target.active),
        "inactive_targets": sum(1 for target in targets if not target.active),
        "fetched": sum(1 for row in metadata_rows if row["status"] == "fetched"),
        "reused": sum(1 for row in metadata_rows if row["status"] == "reused"),
        "blocked": sum(1 for row in metadata_rows if row["status"] == "blocked"),
        "fetch_errors": sum(1 for row in metadata_rows if row["status"] == "fetch_error"),
        "raw_html_stored": sum(1 for row in metadata_rows if row.get("raw_storage") == "stored"),
        "raw_html_reused": sum(1 for row in metadata_rows if row.get("raw_storage") == "reused"),
        "raw_html_deduplicated": sum(1 for row in metadata_rows if row.get("raw_storage") == "deduplicated"),
        "metadata_path": str(metadata_path),
    }
