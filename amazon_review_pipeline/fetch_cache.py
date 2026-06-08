from __future__ import annotations

import json
from pathlib import Path

from amazon_review_pipeline.models import Target
from amazon_review_pipeline.utils import clean_text, utc_timestamp


REUSABLE_STATUSES = {"fetched", "reused"}


def find_reusable_fetch(raw_root: Path, target: Target, exclude_dir: Path | None = None) -> dict | None:
    candidates = []
    for row, metadata_dir in iter_fetch_metadata_rows(raw_root, exclude_dir):
        if row.get("target_id") != target.target_id:
            continue
        if row.get("status") not in REUSABLE_STATUSES:
            continue
        if row.get("blocked_or_signin"):
            continue
        html_path = resolve_metadata_html_path(row, metadata_dir, target.target_id)
        if not html_path:
            continue
        candidate = dict(row)
        candidate["html_path"] = str(html_path)
        candidates.append(candidate)
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (row.get("fetched_at") or "", row.get("run_id") or ""))[-1]


def reuse_fetch_metadata(target: Target, existing: dict) -> dict:
    return {
        "target_id": target.target_id,
        "asin": target.asin or existing.get("asin"),
        "requested_url": target.url,
        "final_url": existing.get("final_url") or existing.get("requested_url") or target.url,
        "status": "reused",
        "status_code": existing.get("status_code"),
        "fetched_at": existing.get("fetched_at"),
        "html_path": existing.get("html_path"),
        "content_hash": existing.get("content_hash"),
        "blocked_or_signin": False,
        "response_bytes": existing.get("response_bytes") or 0,
        "page_title": existing.get("page_title"),
        "product_title": existing.get("product_title"),
        "error_message": None,
        "raw_storage": "reused",
        "reused_from_run_id": existing.get("run_id"),
        "reused_from_html_path": existing.get("html_path"),
        "reused_at": utc_timestamp(),
    }


def build_content_hash_index(raw_root: Path, exclude_dir: Path | None = None) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for row, metadata_dir in iter_fetch_metadata_rows(raw_root, exclude_dir):
        content_hash = clean_text(row.get("content_hash"))
        if not content_hash:
            continue
        html_path = resolve_metadata_html_path(row, metadata_dir, row.get("target_id") or "")
        if html_path and content_hash not in index:
            index[content_hash] = html_path
    return index


def iter_fetch_metadata_rows(raw_root: Path, exclude_dir: Path | None = None):
    if not raw_root.exists():
        return
    exclude_resolved = exclude_dir.resolve() if exclude_dir and exclude_dir.exists() else None
    for metadata_path in sorted(raw_root.glob("*/fetch_metadata.jsonl")):
        metadata_dir = metadata_path.parent
        if metadata_dir.name == "latest":
            continue
        if exclude_resolved and metadata_dir.resolve() == exclude_resolved:
            continue
        with metadata_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line), metadata_dir


def resolve_metadata_html_path(metadata: dict, metadata_dir: Path, target_id: str) -> Path | None:
    html_path_value = clean_text(metadata.get("html_path"))
    candidates = []
    if html_path_value:
        html_path = Path(html_path_value)
        candidates.append(html_path)
        if not html_path.is_absolute():
            candidates.append(metadata_dir / html_path)
            candidates.append(metadata_dir / html_path.name)
    if target_id:
        candidates.append(metadata_dir / f"{target_id}.html")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None
