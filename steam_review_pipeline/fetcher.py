from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Callable

import requests

from steam_review_pipeline.config import (
    DEFAULT_LANGUAGE,
    DEFAULT_NUM_PER_PAGE,
    DEFAULT_PURCHASE_TYPE,
    DEFAULT_REVIEW_TYPE,
    MAX_NUM_PER_PAGE,
    RETRYABLE_STATUS_CODES,
    STEAM_REVIEWS_BASE_URL,
    VALID_REVIEW_FILTERS,
)
from steam_review_pipeline.files import write_json
from steam_review_pipeline.models import SteamApp
from steam_review_pipeline.utils import clean_text, utc_timestamp

SAFE_AUTHOR_FIELDS = {
    "playtime_forever",
    "playtime_last_two_weeks",
    "playtime_at_review",
    "last_played",
}


def fetch_apps(
    apps: list[SteamApp],
    output_dir: Path,
    review_filter: str = "updated",
    language: str = DEFAULT_LANGUAGE,
    purchase_type: str = DEFAULT_PURCHASE_TYPE,
    review_type: str = DEFAULT_REVIEW_TYPE,
    num_per_page: int = DEFAULT_NUM_PER_PAGE,
    max_pages_per_app: int = 50,
    timeout: float = 20.0,
    request_delay_seconds: float = 0.0,
    max_attempts: int = 3,
    retry_delay_seconds: float = 5.0,
    max_runtime_seconds: float | None = None,
    high_water_by_app: dict[str, int] | None = None,
    use_high_water_stop: bool = False,
    session: requests.Session | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    validate_fetch_options(review_filter, num_per_page, max_pages_per_app)
    output_dir.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()

    page_reports: list[dict] = []
    deadline = time.monotonic() + max_runtime_seconds if max_runtime_seconds and max_runtime_seconds > 0 else None
    for app_index, app in enumerate(apps, start=1):
        if deadline is not None and time.monotonic() >= deadline:
            break
        app_reports = fetch_app_reviews(
            app,
            output_dir,
            review_filter=review_filter,
            language=language,
            purchase_type=purchase_type,
            review_type=review_type,
            num_per_page=num_per_page,
            max_pages_per_app=max_pages_per_app,
            timeout=timeout,
            max_attempts=max_attempts,
            retry_delay_seconds=retry_delay_seconds,
            deadline=deadline,
            high_water_timestamp=(high_water_by_app or {}).get(app.app_id, 0) if use_high_water_stop else None,
            session=session,
            sleep_fn=sleep_fn,
        )
        page_reports.extend(app_reports)
        if app_index < len(apps) and request_delay_seconds > 0:
            sleep_fn(request_delay_seconds)

    return {
        "raw_dir": str(output_dir),
        "app_count": len(apps),
        "page_count": len(page_reports),
        "review_count": sum(int(row.get("review_count") or 0) for row in page_reports),
        "fetched_pages": sum(1 for row in page_reports if row.get("status") == "fetched"),
        "empty_pages": sum(1 for row in page_reports if row.get("status") == "empty"),
        "fetch_errors": sum(1 for row in page_reports if row.get("status") == "fetch_error"),
        "rate_limited_pages": sum(1 for row in page_reports if row.get("status_code") == 429),
        "capped_apps": sorted({row["app_id"] for row in page_reports if row.get("terminal_reason") == "page_cap_reached"}),
        "page_reports": page_reports,
    }


def fetch_app_reviews(
    app: SteamApp,
    output_dir: Path,
    review_filter: str = "updated",
    language: str = DEFAULT_LANGUAGE,
    purchase_type: str = DEFAULT_PURCHASE_TYPE,
    review_type: str = DEFAULT_REVIEW_TYPE,
    num_per_page: int = DEFAULT_NUM_PER_PAGE,
    max_pages_per_app: int = 50,
    timeout: float = 20.0,
    max_attempts: int = 3,
    retry_delay_seconds: float = 5.0,
    deadline: float | None = None,
    high_water_timestamp: int | None = None,
    session: requests.Session | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[dict]:
    validate_fetch_options(review_filter, num_per_page, max_pages_per_app)
    session = session or requests.Session()
    reports: list[dict] = []
    cursor = "*"
    page_number = 1

    while max_pages_per_app == 0 or page_number <= max_pages_per_app:
        if deadline is not None and time.monotonic() >= deadline:
            if reports:
                reports[-1]["terminal_reason"] = reports[-1].get("terminal_reason") or "runtime_limit_reached"
            break
        result = request_review_page(
            session,
            app_id=app.app_id,
            cursor=cursor,
            review_filter=review_filter,
            language=language,
            purchase_type=purchase_type,
            review_type=review_type,
            num_per_page=num_per_page,
            timeout=timeout,
            max_attempts=max_attempts,
            retry_delay_seconds=retry_delay_seconds,
            sleep_fn=sleep_fn,
        )
        fetched_at = utc_timestamp()
        page_path = output_dir / f"app_{app.app_id}_page_{page_number:04d}.json"
        payload = result.get("payload")
        sanitized_payload = sanitize_payload_for_storage(payload) if isinstance(payload, dict) else None
        if sanitized_payload is not None:
            write_json(page_path, sanitized_payload)
        reviews = payload.get("reviews", []) if isinstance(payload, dict) else []
        query_summary = payload.get("query_summary", {}) if isinstance(payload, dict) else {}
        next_cursor = clean_text(payload.get("cursor")) if isinstance(payload, dict) else None
        status = result["status"]
        max_timestamp_updated = max_review_timestamp(reviews, "timestamp_updated")
        min_timestamp_updated = min_review_timestamp(reviews, "timestamp_updated")
        terminal_reason = terminal_reason_for_page(
            status=status,
            review_count=len(reviews),
            next_cursor=next_cursor,
            previous_cursor=cursor,
            page_number=page_number,
            max_pages_per_app=max_pages_per_app,
        )
        if status == "fetched" and high_water_timestamp is not None and page_caught_up(reviews, high_water_timestamp):
            terminal_reason = "caught_up_to_existing_reviews"
        if terminal_reason is None and deadline is not None and time.monotonic() >= deadline:
            terminal_reason = "runtime_limit_reached"
        report = {
            "app_id": app.app_id,
            "app_name": app.app_name,
            "page_number": page_number,
            "request_url": build_review_url(
                app.app_id,
                cursor=cursor,
                review_filter=review_filter,
                language=language,
                purchase_type=purchase_type,
                review_type=review_type,
                num_per_page=num_per_page,
            ),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "status": status if reviews else ("empty" if status == "fetched" else status),
            "status_code": result.get("status_code"),
            "fetched_at": fetched_at,
            "raw_json_path": str(page_path) if sanitized_payload is not None else None,
            "response_bytes": response_size(sanitized_payload),
            "review_count": len(reviews),
            "total_reviews": query_summary.get("total_reviews"),
            "total_positive": query_summary.get("total_positive"),
            "total_negative": query_summary.get("total_negative"),
            "max_timestamp_updated": max_timestamp_updated,
            "min_timestamp_updated": min_timestamp_updated,
            "attempt_count": result.get("attempt_count", 1),
            "error_message": result.get("error_message"),
            "terminal_reason": terminal_reason,
        }
        reports.append(report)
        if terminal_reason:
            break
        cursor = next_cursor or ""
        page_number += 1

    return reports


def request_review_page(
    session: requests.Session,
    app_id: str,
    cursor: str,
    review_filter: str,
    language: str,
    purchase_type: str,
    review_type: str,
    num_per_page: int,
    timeout: float,
    max_attempts: int,
    retry_delay_seconds: float,
    sleep_fn: Callable[[float], None],
) -> dict:
    params = review_params(cursor, review_filter, language, purchase_type, review_type, num_per_page)
    url = f"{STEAM_REVIEWS_BASE_URL}/{app_id}"
    last_status_code = None
    for attempt in range(1, max(max_attempts, 1) + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            if attempt < max_attempts:
                sleep_fn(retry_delay_seconds * attempt)
                continue
            return {
                "status": "fetch_error",
                "status_code": None,
                "payload": None,
                "attempt_count": attempt,
                "error_message": str(exc),
            }
        last_status_code = response.status_code
        if response.status_code in RETRYABLE_STATUS_CODES and attempt < max_attempts:
            sleep_fn(retry_delay_seconds * attempt)
            continue
        if response.status_code != 200:
            return {
                "status": "fetch_error",
                "status_code": response.status_code,
                "payload": None,
                "attempt_count": attempt,
                "error_message": f"HTTP {response.status_code}",
            }
        try:
            payload = response.json()
        except ValueError as exc:
            return {
                "status": "fetch_error",
                "status_code": response.status_code,
                "payload": None,
                "attempt_count": attempt,
                "error_message": f"Invalid JSON: {exc}",
            }
        if int(payload.get("success") or 0) != 1:
            return {
                "status": "fetch_error",
                "status_code": response.status_code,
                "payload": payload,
                "attempt_count": attempt,
                "error_message": f"Steam API returned success={payload.get('success')}",
            }
        return {
            "status": "fetched",
            "status_code": response.status_code,
            "payload": payload,
            "attempt_count": attempt,
            "error_message": None,
        }
    return {
        "status": "fetch_error",
        "status_code": last_status_code,
        "payload": None,
        "attempt_count": max_attempts,
        "error_message": "Retry attempts exhausted",
    }


def build_review_url(
    app_id: str,
    cursor: str = "*",
    review_filter: str = "updated",
    language: str = DEFAULT_LANGUAGE,
    purchase_type: str = DEFAULT_PURCHASE_TYPE,
    review_type: str = DEFAULT_REVIEW_TYPE,
    num_per_page: int = DEFAULT_NUM_PER_PAGE,
) -> str:
    request = requests.Request(
        "GET",
        f"{STEAM_REVIEWS_BASE_URL}/{app_id}",
        params=review_params(cursor, review_filter, language, purchase_type, review_type, num_per_page),
    )
    return request.prepare().url or ""


def review_params(
    cursor: str,
    review_filter: str,
    language: str,
    purchase_type: str,
    review_type: str,
    num_per_page: int,
) -> dict:
    return {
        "json": "1",
        "filter": review_filter,
        "language": language,
        "purchase_type": purchase_type,
        "review_type": review_type,
        "num_per_page": str(num_per_page),
        "cursor": cursor,
    }


def sanitize_payload_for_storage(payload: dict) -> dict:
    sanitized = copy.deepcopy(payload)
    for review in sanitized.get("reviews", []):
        author = review.get("author")
        if isinstance(author, dict):
            review["author"] = {field: author[field] for field in SAFE_AUTHOR_FIELDS if field in author}
    return sanitized


def terminal_reason_for_page(
    status: str,
    review_count: int,
    next_cursor: str | None,
    previous_cursor: str,
    page_number: int,
    max_pages_per_app: int,
) -> str | None:
    if status != "fetched":
        return "fetch_error"
    if review_count == 0:
        return "empty_page"
    if max_pages_per_app and page_number >= max_pages_per_app:
        return "page_cap_reached"
    if not next_cursor:
        return "missing_next_cursor"
    if next_cursor == previous_cursor:
        return "cursor_not_advancing"
    return None


def page_caught_up(reviews: list[dict], high_water_timestamp: int) -> bool:
    if high_water_timestamp <= 0 or not reviews:
        return False
    timestamps = [int(review.get("timestamp_updated") or 0) for review in reviews]
    return max(timestamps, default=0) <= high_water_timestamp


def max_review_timestamp(reviews: list[dict], field: str) -> int | None:
    timestamps = review_timestamps(reviews, field)
    return max(timestamps) if timestamps else None


def min_review_timestamp(reviews: list[dict], field: str) -> int | None:
    timestamps = review_timestamps(reviews, field)
    return min(timestamps) if timestamps else None


def review_timestamps(reviews: list[dict], field: str) -> list[int]:
    timestamps: list[int] = []
    for review in reviews:
        value = review.get(field)
        if value is None:
            continue
        try:
            timestamps.append(int(value))
        except (TypeError, ValueError):
            continue
    return timestamps


def response_size(payload: dict | None) -> int:
    if payload is None:
        return 0
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def validate_fetch_options(review_filter: str, num_per_page: int, max_pages_per_app: int) -> None:
    if review_filter not in VALID_REVIEW_FILTERS:
        raise ValueError(f"review_filter must be one of: {', '.join(sorted(VALID_REVIEW_FILTERS))}")
    if num_per_page < 1 or num_per_page > MAX_NUM_PER_PAGE:
        raise ValueError(f"num_per_page must be between 1 and {MAX_NUM_PER_PAGE}")
    if max_pages_per_app < 0:
        raise ValueError("max_pages_per_app must be 0 or greater")
