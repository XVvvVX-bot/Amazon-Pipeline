from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable

from steam_review_pipeline.fetcher import fetch_apps
from steam_review_pipeline.files import write_json, write_jsonl
from steam_review_pipeline.postgres_database import (
    app_high_water_marks,
    app_sync_states,
    load_pipeline_run_postgres,
    mask_database_url,
    update_app_sync_states,
    validate_postgres,
)
from steam_review_pipeline.targets import load_targets
from steam_review_pipeline.utils import make_run_id, utc_timestamp


def run_daily_pipeline(args: argparse.Namespace, sleep_fn: Callable[[float], None] = time.sleep) -> dict:
    started_at = utc_timestamp()
    run_id = make_run_id()
    raw_dir = args.raw_root / run_id
    reports_dir = args.reports_root / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    apps = [app for app in load_targets(args.targets) if app.active]
    use_delta_stop = args.review_filter == "updated" and not getattr(args, "disable_delta_stop", False)
    high_water_by_app = app_high_water_marks(args.database_url, [app.app_id for app in apps]) if use_delta_stop else {}
    sync_state_before = app_sync_states(args.database_url, [app.app_id for app in apps])
    max_runtime_seconds = args.max_runtime_minutes * 60 if getattr(args, "max_runtime_minutes", 0) > 0 else None
    fetch_report = fetch_apps(
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
        max_runtime_seconds=max_runtime_seconds,
        high_water_by_app=high_water_by_app,
        use_high_water_stop=use_delta_stop,
        sleep_fn=sleep_fn,
    )
    for row in fetch_report["page_reports"]:
        row["run_id"] = run_id
    write_jsonl(raw_dir / "review_pages.jsonl", fetch_report["page_reports"])
    write_json(raw_dir / "fetch_report.json", fetch_report)

    load_summary = load_pipeline_run_postgres(args.database_url, raw_dir, args.targets)
    completed_at = utc_timestamp()
    sync_state_summary = update_app_sync_states(
        args.database_url,
        fetch_report["page_reports"],
        run_id,
        args.review_filter,
        started_at,
        completed_at,
    )
    validation_report = validate_postgres(args.database_url, None)
    validation_path = reports_dir / "validation_report.json"
    write_json(validation_path, validation_report)

    report = {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "targets_path": str(args.targets),
        "raw_dir": str(raw_dir),
        "database_url": mask_database_url(args.database_url),
        "storage_backend": "postgres",
        "review_filter": args.review_filter,
        "language": args.language,
        "max_pages_per_app": args.max_pages_per_app,
        "max_runtime_minutes": args.max_runtime_minutes,
        "delta_stop_enabled": use_delta_stop,
        "high_water_by_app": high_water_by_app,
        "sync_state_before": sync_state_before,
        "sync_state_summary": sync_state_summary,
        "app_count": len(apps),
        "fetch_summary": summarize_fetch(fetch_report),
        "load_summary": load_summary,
        "validation_report_path": str(validation_path),
        "report_path": str(reports_dir / "daily_report.json"),
    }
    write_json(reports_dir / "daily_report.json", report)
    return report


def summarize_fetch(fetch_report: dict) -> dict:
    page_reports = fetch_report.get("page_reports", [])
    terminal_reasons: dict[str, int] = {}
    for row in page_reports:
        reason = row.get("terminal_reason")
        if reason:
            terminal_reasons[reason] = terminal_reasons.get(reason, 0) + 1
    return {
        "page_count": len(page_reports),
        "fetched_pages": fetch_report.get("fetched_pages", 0),
        "empty_pages": fetch_report.get("empty_pages", 0),
        "fetch_errors": fetch_report.get("fetch_errors", 0),
        "rate_limited_pages": fetch_report.get("rate_limited_pages", 0),
        "reviews_seen": fetch_report.get("review_count", 0),
        "capped_apps": fetch_report.get("capped_apps", []),
        "terminal_reasons": terminal_reasons,
    }
