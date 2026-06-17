from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from app_store_source_evaluation.targets import PublicAppTarget, active_targets, load_public_app_targets


USER_AGENT = "ScienciaAI-AppStoreSourceEvaluation/0.1"
REVIEW_MARKERS = (
    "ratings and reviews",
    "rating and reviews",
    "customer reviews",
    "reviews",
)
PAGINATION_MARKERS = (
    "next page",
    "load more",
    "show more",
    "see all reviews",
    "pagination",
)
ACCESS_CONTROL_MARKERS = (
    "captcha",
    "unusual traffic",
    "sign in to continue",
    "verify you are human",
    "access denied",
)


@dataclass(frozen=True)
class StorefrontSmokeResult:
    platform: str
    app_name: str
    app_identifier: str
    url: str
    status_code: int | None
    ok: bool
    response_bytes: int
    content_type: str | None
    fetched_at: str
    has_review_marker: bool
    has_rating_marker: bool
    has_pagination_marker: bool
    has_structured_data_marker: bool
    has_access_control_marker: bool
    review_signal_count: int
    notes: str


def run_storefront_smoke(
    targets_path: Path,
    output_path: Path | None = None,
    *,
    limit: int | None = None,
    timeout_seconds: float = 20,
    delay_seconds: float = 1,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    targets = active_targets(load_public_app_targets(targets_path))
    if limit is not None:
        targets = targets[:limit]

    owned_session = session is None
    http = session or requests.Session()
    results: list[StorefrontSmokeResult] = []
    try:
        for index, target in enumerate(targets):
            if index and delay_seconds:
                time.sleep(delay_seconds)
            results.append(fetch_storefront(target, "google_play", http, timeout_seconds))
            results.append(fetch_storefront(target, "apple_app_store", http, timeout_seconds))
    finally:
        if owned_session:
            http.close()

    report = build_smoke_report(targets_path, results)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def fetch_storefront(
    target: PublicAppTarget,
    platform: str,
    session: requests.Session,
    timeout_seconds: float,
) -> StorefrontSmokeResult:
    if platform == "google_play":
        url = target.google_play_url
        app_identifier = target.google_play_package
    elif platform == "apple_app_store":
        url = target.apple_app_store_url
        app_identifier = target.apple_app_id
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    fetched_at = datetime.now(UTC).isoformat()
    try:
        response = session.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": USER_AGENT,
            },
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        return StorefrontSmokeResult(
            platform=platform,
            app_name=target.app_name,
            app_identifier=app_identifier,
            url=url,
            status_code=None,
            ok=False,
            response_bytes=0,
            content_type=None,
            fetched_at=fetched_at,
            has_review_marker=False,
            has_rating_marker=False,
            has_pagination_marker=False,
            has_structured_data_marker=False,
            has_access_control_marker=False,
            review_signal_count=0,
            notes=f"request failed: {exc}",
        )

    html = response.text or ""
    scan = scan_storefront_html(html)
    return StorefrontSmokeResult(
        platform=platform,
        app_name=target.app_name,
        app_identifier=app_identifier,
        url=url,
        status_code=response.status_code,
        ok=200 <= response.status_code < 300,
        response_bytes=len(response.content or b""),
        content_type=response.headers.get("content-type"),
        fetched_at=fetched_at,
        has_review_marker=scan["has_review_marker"],
        has_rating_marker=scan["has_rating_marker"],
        has_pagination_marker=scan["has_pagination_marker"],
        has_structured_data_marker=scan["has_structured_data_marker"],
        has_access_control_marker=scan["has_access_control_marker"],
        review_signal_count=scan["review_signal_count"],
        notes=scan["notes"],
    )


def scan_storefront_html(html: str) -> dict[str, Any]:
    lowered = html.lower()
    review_signal_count = sum(1 for marker in REVIEW_MARKERS if marker in lowered)
    has_rating_marker = "rating" in lowered or "star" in lowered
    has_structured_data_marker = "application/ld+json" in lowered or "aggregaterating" in lowered
    has_pagination_marker = any(marker in lowered for marker in PAGINATION_MARKERS)
    has_access_control_marker = any(marker in lowered for marker in ACCESS_CONTROL_MARKERS)
    visible_review_like_text = bool(re.search(r"\b[1-5](?:\.0)?\s*(?:star|stars)\b", lowered))

    notes = "public detail page fetched; no documented public full-review pagination proven"
    if has_access_control_marker:
        notes = "access-control marker detected; do not retry with bypass behavior"
    elif visible_review_like_text and review_signal_count:
        notes = "review/rating markers detected on public detail page; full-review depth still unproven"

    return {
        "has_review_marker": review_signal_count > 0,
        "has_rating_marker": has_rating_marker,
        "has_pagination_marker": has_pagination_marker,
        "has_structured_data_marker": has_structured_data_marker,
        "has_access_control_marker": has_access_control_marker,
        "review_signal_count": review_signal_count,
        "notes": notes,
    }


def build_smoke_report(targets_path: Path, results: list[StorefrontSmokeResult]) -> dict[str, Any]:
    by_platform: dict[str, dict[str, int]] = {}
    for result in results:
        stats = by_platform.setdefault(
            result.platform,
            {
                "requests": 0,
                "ok": 0,
                "review_markers": 0,
                "pagination_markers": 0,
                "access_control_markers": 0,
            },
        )
        stats["requests"] += 1
        stats["ok"] += int(result.ok)
        stats["review_markers"] += int(result.has_review_marker)
        stats["pagination_markers"] += int(result.has_pagination_marker)
        stats["access_control_markers"] += int(result.has_access_control_marker)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "targets_path": str(targets_path),
        "ethical_boundary": (
            "Fetches only public app detail pages with no login, cookies, CAPTCHA solving, "
            "proxy rotation, or hidden review endpoints."
        ),
        "summary": by_platform,
        "results": [asdict(result) for result in results],
    }
