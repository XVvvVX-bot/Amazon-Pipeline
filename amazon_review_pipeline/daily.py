from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from amazon_review_pipeline.database import export_reviews, load_pipeline_run, validate_database
from amazon_review_pipeline.discovery import BESTSELLERS_URL, DEFAULT_DISCOVERY_ROOT, run_discovery
from amazon_review_pipeline.extraction import extract_reviews_from_raw_dir
from amazon_review_pipeline.fetch_cache import build_content_hash_index
from amazon_review_pipeline.fetcher import fetch_target
from amazon_review_pipeline.files import infer_run_id, load_fetch_metadata, update_latest_dir, write_jsonl
from amazon_review_pipeline.models import Target
from amazon_review_pipeline.targets import load_targets
from amazon_review_pipeline.utils import make_run_id, utc_timestamp


DEFAULT_STATE_PATH = Path("data/state/pipeline_state.json")
DEFAULT_REPORTS_ROOT = Path("data/reports")
SUCCESS_STATUSES = {"fetched", "reused"}
RETRYABLE_STATUSES = {"fetch_error"}


def run_daily_pipeline(
    args: argparse.Namespace,
    sleep_fn: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> dict:
    rng = rng or random.Random()
    started_at = utc_timestamp()
    start_monotonic = time.monotonic()
    daily_run_id = make_run_id()
    reports_dir = args.reports_root / daily_run_id
    reports_dir.mkdir(parents=True, exist_ok=True)

    state = load_pipeline_state(args.state)
    discovery_report = run_daily_discovery(args)
    targets = load_targets(args.targets)
    discovered_target_ids = read_discovered_target_ids(discovery_report)
    sync_state_with_targets(state, targets, discovered_target_ids, started_at)
    due_targets = select_due_targets(
        targets,
        state,
        now=started_at,
        stale_days=args.stale_days,
        blocked_cooldown_days=args.blocked_cooldown_days,
        error_retry_days=args.error_retry_days,
    )

    content_hash_index = build_content_hash_index(args.raw_root)
    batches = chunk_targets(due_targets, args.batch_size)
    batch_reports: list[dict] = []
    stop_reason = "queue_drained"
    cooldown_reports: list[dict] = []

    for batch_index, batch_targets in enumerate(batches, start=1):
        batch_report = run_daily_batch(
            batch_targets=batch_targets,
            batch_index=batch_index,
            daily_run_id=daily_run_id,
            args=args,
            content_hash_index=content_hash_index,
            sleep_fn=sleep_fn,
            rng=rng,
        )
        batch_reports.append(batch_report)
        apply_fetch_metadata_to_state(state, batch_report["metadata_rows"])
        save_pipeline_state(args.state, state)

        stop_reason = safety_stop_reason(
            batch_reports,
            start_monotonic=start_monotonic,
            max_runtime_minutes=args.max_runtime_minutes,
            max_block_rate=args.max_block_rate,
            max_consecutive_blocked=args.max_consecutive_blocked,
        )
        if stop_reason != "continue":
            break

        if batch_index < len(batches):
            cooldown = next_batch_cooldown(args, batch_reports, rng)
            batch_report["cooldown_after_batch"] = cooldown
            cooldown_reports.append(cooldown)
            if cooldown["seconds"] > 0:
                sleep_fn(cooldown["seconds"])

    validation_report = validate_database(args.db, None)
    validation_path = reports_dir / "validation_report.json"
    validation_path.write_text(json.dumps(validation_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    export_summary = export_reviews(args.db, args.export_csv, "csv", None)
    completed_at = utc_timestamp()
    report = {
        "daily_run_id": daily_run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "targets_path": str(args.targets),
        "state_path": str(args.state),
        "discovery_report": discovery_report,
        "queue": {
            "due_targets": len(due_targets),
            "batch_size": args.batch_size,
            "batches_planned": len(batches),
            "batches_completed": len(batch_reports),
            "remaining_targets": max(len(due_targets) - sum(len(batch["target_ids"]) for batch in batch_reports), 0),
        },
        "pacing": {
            "target_delay_seconds": delay_range_from_args(args, "target_delay_seconds", "target_delay_min_seconds", "target_delay_max_seconds"),
            "batch_cooldown_seconds": delay_range_from_args(
                args,
                "batch_cooldown_minutes",
                "batch_cooldown_min_minutes",
                "batch_cooldown_max_minutes",
                multiplier=60,
            ),
            "adaptive_slow_block_rate": getattr(args, "adaptive_slow_block_rate", 0.05),
            "adaptive_strong_slow_block_rate": getattr(args, "adaptive_strong_slow_block_rate", 0.15),
            "adaptive_slowdown_multiplier": getattr(args, "adaptive_slowdown_multiplier", 2.0),
            "adaptive_strong_slowdown_multiplier": getattr(args, "adaptive_strong_slowdown_multiplier", 3.0),
            "cooldowns": cooldown_reports,
        },
        "stop_reason": stop_reason if stop_reason != "continue" else "queue_drained",
        "batch_reports": strip_batch_metadata(batch_reports),
        "validation_report_path": str(validation_path),
        "export_summary": export_summary,
    }
    report_path = reports_dir / "daily_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    save_pipeline_state(args.state, state)
    return report


def run_daily_discovery(args: argparse.Namespace) -> dict:
    discovery_args = argparse.Namespace(
        seed_url=args.seed_url,
        targets=args.targets,
        max_seed_pages=args.max_seed_pages,
        max_departments=getattr(args, "max_departments", 12),
        max_subdepartment_depth=getattr(args, "max_subdepartment_depth", 1),
        max_subdepartment_pages=getattr(args, "max_subdepartment_pages", 25),
        max_pages_per_seed=getattr(args, "max_pages_per_seed", 1),
        max_products_per_page=args.max_products_per_page,
        delay=args.discovery_delay,
        timeout=args.timeout,
        discovery_root=args.discovery_root,
        inactive=False,
    )
    return run_discovery(discovery_args)


def run_daily_batch(
    batch_targets: list[Target],
    batch_index: int,
    daily_run_id: str,
    args: argparse.Namespace,
    content_hash_index: dict[str, Path],
    sleep_fn: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> dict:
    rng = rng or random.Random()
    batch_run_id = f"{daily_run_id}_batch{batch_index:02d}"
    raw_dir = args.raw_root / batch_run_id
    parsed_dir = args.parsed_root / batch_run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    metadata_rows = []
    target_delay_range = delay_range_from_args(args, "target_delay_seconds", "target_delay_min_seconds", "target_delay_max_seconds")
    target_delay_seconds: list[float] = []
    for target_index, target in enumerate(batch_targets, start=1):
        metadata = fetch_target(
            target,
            raw_dir,
            args.timeout,
            content_hash_index,
            fetch_method=getattr(args, "fetch_method", "requests"),
        )
        metadata["run_id"] = batch_run_id
        metadata_rows.append(metadata)
        if target_index < len(batch_targets):
            delay_seconds = sample_delay(target_delay_range, rng)
            target_delay_seconds.append(delay_seconds)
            if delay_seconds > 0:
                sleep_fn(delay_seconds)

    metadata_path = raw_dir / "fetch_metadata.jsonl"
    write_jsonl(metadata_rows, metadata_path)
    update_latest_dir(raw_dir, args.raw_root / "latest")

    metadata_by_target = load_fetch_metadata(metadata_path)
    reviews, target_reports = extract_reviews_from_raw_dir(raw_dir, metadata_by_target)
    parse_report_path = parsed_dir / "parse_report.json"
    parse_report = {
        "raw_dir": str(raw_dir),
        "keep_jsonl": False,
        "reviews_path": None,
        "target_count": len(target_reports),
        "review_count": len(reviews),
        "targets": target_reports,
    }
    parse_report_path.write_text(json.dumps(parse_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    load_summary = load_pipeline_run(args.db, parsed_dir, raw_dir, args.targets)

    return {
        "batch_index": batch_index,
        "run_id": infer_run_id(raw_dir, metadata_by_target),
        "target_ids": [target.target_id for target in batch_targets],
        "raw_dir": str(raw_dir),
        "parsed_dir": str(parsed_dir),
        "metadata_path": str(metadata_path),
        "parse_report_path": str(parse_report_path),
        "metadata_rows": metadata_rows,
        "pacing": {
            "target_delay_seconds": summarize_delays(target_delay_seconds),
        },
        "fetch_summary": summarize_metadata(metadata_rows),
        "parse_summary": {
            "target_count": len(target_reports),
            "review_count": len(reviews),
        },
        "load_summary": load_summary,
    }


def load_pipeline_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "targets": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("version", 1)
    state.setdefault("targets", {})
    return state


def save_pipeline_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_discovered_target_ids(discovery_report: dict) -> set[str]:
    products_path = Path(discovery_report.get("products_path") or "")
    if not products_path.exists():
        return set()
    target_ids = set()
    with products_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if row.get("target_id"):
                    target_ids.add(row["target_id"])
    return target_ids


def sync_state_with_targets(state: dict, targets: list[Target], discovered_target_ids: set[str], now: str) -> None:
    states = state.setdefault("targets", {})
    active_ids = {target.target_id for target in targets}
    for target in targets:
        entry = states.setdefault(
            target.target_id,
            {
                "asin": target.asin,
                "first_seen_at": now,
                "block_count": 0,
                "fetch_error_count": 0,
                "discovery_count": 0,
            },
        )
        entry["asin"] = target.asin
        entry["active"] = target.active
        if target.target_id in discovered_target_ids:
            entry["last_discovered_at"] = now
            entry["discovery_count"] = int(entry.get("discovery_count") or 0) + 1
    for target_id, entry in states.items():
        if target_id not in active_ids:
            entry["active"] = False
    state["last_discovery_at"] = now


def select_due_targets(
    targets: list[Target],
    state: dict,
    now: str,
    stale_days: int,
    blocked_cooldown_days: int,
    error_retry_days: int,
) -> list[Target]:
    states = state.get("targets", {})
    prioritized: list[tuple[int, str, Target]] = []
    for target in targets:
        if not target.active:
            continue
        entry = states.get(target.target_id, {})
        if is_recently_blocked(entry, now, blocked_cooldown_days):
            continue
        priority = target_priority(target, entry, now, stale_days, error_retry_days)
        if priority is not None:
            prioritized.append((priority, entry.get("last_successful_fetch_at") or "", target))
    return [target for _, _, target in sorted(prioritized, key=lambda item: (item[0], item[1], item[2].target_id))]


def target_priority(target: Target, entry: dict, now: str, stale_days: int, error_retry_days: int) -> int | None:
    if not entry.get("last_fetch_attempt_at"):
        return 1
    if entry.get("last_status") in RETRYABLE_STATUSES and days_since(entry.get("last_fetch_attempt_at"), now) >= error_retry_days:
        return 2
    if not entry.get("last_successful_fetch_at"):
        return 3
    if days_since(entry.get("last_successful_fetch_at"), now) >= stale_days:
        return 4
    return None


def is_recently_blocked(entry: dict, now: str, blocked_cooldown_days: int) -> bool:
    if entry.get("last_status") != "blocked":
        return False
    last_attempt = entry.get("last_fetch_attempt_at")
    return last_attempt is not None and days_since(last_attempt, now) < blocked_cooldown_days


def days_since(timestamp: str | None, now: str) -> float:
    if not timestamp:
        return float("inf")
    return max((parse_timestamp(now) - parse_timestamp(timestamp)).total_seconds() / 86400, 0)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def chunk_targets(targets: list[Target], batch_size: int) -> list[list[Target]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0")
    return [targets[index : index + batch_size] for index in range(0, len(targets), batch_size)]


def delay_range_from_args(
    args: argparse.Namespace,
    fixed_attr: str,
    min_attr: str,
    max_attr: str,
    multiplier: float = 1.0,
) -> dict[str, float]:
    min_value = getattr(args, min_attr, None)
    max_value = getattr(args, max_attr, None)
    if min_value is None and max_value is None:
        fixed_value = float(getattr(args, fixed_attr, 0.0) or 0.0)
        min_seconds = fixed_value * multiplier
        max_seconds = fixed_value * multiplier
    else:
        min_seconds = float(min_value if min_value is not None else 0.0) * multiplier
        max_seconds = float(max_value if max_value is not None else min_seconds / multiplier) * multiplier
    if min_seconds < 0 or max_seconds < 0:
        raise ValueError(f"{min_attr} and {max_attr} must be non-negative")
    if max_seconds < min_seconds:
        raise ValueError(f"{max_attr} must be greater than or equal to {min_attr}")
    return {
        "min": min_seconds,
        "max": max_seconds,
        "fixed": min_seconds == max_seconds,
    }


def sample_delay(delay_range: dict[str, float], rng: random.Random) -> float:
    min_seconds = float(delay_range["min"])
    max_seconds = float(delay_range["max"])
    if max_seconds <= 0:
        return 0.0
    if min_seconds == max_seconds:
        return min_seconds
    return rng.uniform(min_seconds, max_seconds)


def summarize_delays(delays: list[float]) -> dict:
    if not delays:
        return {
            "count": 0,
            "total": 0.0,
            "min": 0.0,
            "max": 0.0,
            "average": 0.0,
        }
    total = sum(delays)
    return {
        "count": len(delays),
        "total": round(total, 3),
        "min": round(min(delays), 3),
        "max": round(max(delays), 3),
        "average": round(total / len(delays), 3),
    }


def next_batch_cooldown(args: argparse.Namespace, batch_reports: list[dict], rng: random.Random) -> dict:
    base_range = delay_range_from_args(
        args,
        "batch_cooldown_minutes",
        "batch_cooldown_min_minutes",
        "batch_cooldown_max_minutes",
        multiplier=60,
    )
    base_seconds = sample_delay(base_range, rng)
    multiplier, reason = adaptive_cooldown_multiplier(args, batch_reports)
    seconds = base_seconds * multiplier
    return {
        "base_seconds": round(base_seconds, 3),
        "multiplier": multiplier,
        "seconds": round(seconds, 3),
        "reason": reason,
        "latest_batch_block_rate": round(batch_block_rate(batch_reports[-1]), 4) if batch_reports else 0.0,
        "cumulative_block_rate": round(cumulative_block_rate(batch_reports), 4),
    }


def adaptive_cooldown_multiplier(args: argparse.Namespace, batch_reports: list[dict]) -> tuple[float, str]:
    if not batch_reports:
        return 1.0, "normal"
    latest_rate = batch_block_rate(batch_reports[-1])
    strong_threshold = float(getattr(args, "adaptive_strong_slow_block_rate", 0.15))
    slow_threshold = float(getattr(args, "adaptive_slow_block_rate", 0.05))
    if latest_rate >= strong_threshold:
        return float(getattr(args, "adaptive_strong_slowdown_multiplier", 3.0)), "strong_slowdown"
    if latest_rate >= slow_threshold:
        return float(getattr(args, "adaptive_slowdown_multiplier", 2.0)), "slowdown"
    return 1.0, "normal"


def batch_block_rate(batch_report: dict) -> float:
    rows = batch_report.get("metadata_rows") or []
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.get("status") == "blocked") / len(rows)


def cumulative_block_rate(batch_reports: list[dict]) -> float:
    rows = [row for batch in batch_reports for row in batch.get("metadata_rows", [])]
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.get("status") == "blocked") / len(rows)


def apply_fetch_metadata_to_state(state: dict, metadata_rows: list[dict]) -> None:
    states = state.setdefault("targets", {})
    for metadata in metadata_rows:
        target_id = metadata.get("target_id")
        if not target_id:
            continue
        entry = states.setdefault(target_id, {"block_count": 0, "fetch_error_count": 0, "discovery_count": 0})
        status = metadata.get("status")
        fetched_at = metadata.get("fetched_at") or utc_timestamp()
        entry["last_fetch_attempt_at"] = fetched_at
        entry["last_status"] = status
        entry["last_status_code"] = metadata.get("status_code")
        entry["latest_content_hash"] = metadata.get("content_hash")
        entry["last_fetch_method"] = metadata.get("fetch_method")
        entry["latest_review_section_detected"] = bool(metadata.get("review_section_detected"))
        entry["last_blocked_reason"] = metadata.get("blocked_reason")
        if status in SUCCESS_STATUSES:
            entry["last_successful_fetch_at"] = fetched_at
            entry["fetch_error_count"] = 0
        elif status == "blocked":
            entry["block_count"] = int(entry.get("block_count") or 0) + 1
        elif status == "fetch_error":
            entry["fetch_error_count"] = int(entry.get("fetch_error_count") or 0) + 1


def summarize_metadata(metadata_rows: list[dict]) -> dict:
    return {
        "targets": len(metadata_rows),
        "fetched": sum(1 for row in metadata_rows if row.get("status") == "fetched"),
        "blocked": sum(1 for row in metadata_rows if row.get("status") == "blocked"),
        "fetch_errors": sum(1 for row in metadata_rows if row.get("status") == "fetch_error"),
        "raw_html_stored": sum(1 for row in metadata_rows if row.get("raw_storage") == "stored"),
        "raw_html_deduplicated": sum(1 for row in metadata_rows if row.get("raw_storage") == "deduplicated"),
        "review_sections_detected": sum(1 for row in metadata_rows if row.get("review_section_detected")),
        "fetch_methods": count_by(metadata_rows, "fetch_method"),
        "blocked_reasons": count_by([row for row in metadata_rows if row.get("blocked_reason")], "blocked_reason"),
    }


def count_by(rows: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def safety_stop_reason(
    batch_reports: list[dict],
    start_monotonic: float,
    max_runtime_minutes: float,
    max_block_rate: float,
    max_consecutive_blocked: int,
) -> str:
    if elapsed_minutes(start_monotonic) >= max_runtime_minutes:
        return "max_runtime_reached"
    metadata_rows = [row for batch in batch_reports for row in batch["metadata_rows"]]
    if not metadata_rows:
        return "continue"
    blocked_count = sum(1 for row in metadata_rows if row.get("status") == "blocked")
    if blocked_count / len(metadata_rows) >= max_block_rate:
        return "max_block_rate_reached"
    if consecutive_blocked(metadata_rows) >= max_consecutive_blocked:
        return "max_consecutive_blocked_reached"
    return "continue"


def elapsed_minutes(start_monotonic: float) -> float:
    return (time.monotonic() - start_monotonic) / 60


def consecutive_blocked(metadata_rows: list[dict]) -> int:
    longest = 0
    current = 0
    for row in metadata_rows:
        if row.get("status") == "blocked":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def strip_batch_metadata(batch_reports: list[dict]) -> list[dict]:
    stripped = []
    for batch in batch_reports:
        row = dict(batch)
        row.pop("metadata_rows", None)
        stripped.append(row)
    return stripped
