from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from amazon_review_pipeline.config import BLOCK_MARKERS, DEFAULT_HEADERS
from amazon_review_pipeline.models import Target
from amazon_review_pipeline.parser import detect_review_section
from amazon_review_pipeline.targets import infer_asin_from_url
from amazon_review_pipeline.utils import clean_text, sha256_text, utc_timestamp


FETCH_METHODS = {"requests", "playwright", "auto"}


@dataclass
class FetchAttempt:
    html: str | None
    final_url: str | None
    status_code: int | None
    fetch_method: str
    rendered: bool
    error_message: str | None = None


def fetch_target(
    target: Target,
    output_dir: Path,
    timeout: float,
    content_hash_index: dict[str, Path] | None = None,
    fetch_method: str = "requests",
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = utc_timestamp()
    fetch_method = normalize_fetch_method(fetch_method)

    if fetch_method == "requests":
        attempt = fetch_via_requests(target, timeout)
        return metadata_from_attempt(target, output_dir, fetched_at, attempt, content_hash_index, attempt_count=1)

    if fetch_method == "playwright":
        attempt = fetch_via_playwright(target, timeout)
        return metadata_from_attempt(target, output_dir, fetched_at, attempt, content_hash_index, attempt_count=1)

    request_attempt = fetch_via_requests(target, timeout)
    if should_keep_first_auto_attempt(request_attempt):
        return metadata_from_attempt(target, output_dir, fetched_at, request_attempt, content_hash_index, attempt_count=1)

    rendered_attempt = fetch_via_playwright(target, timeout)
    if rendered_attempt.html:
        return metadata_from_attempt(target, output_dir, fetched_at, rendered_attempt, content_hash_index, attempt_count=2)

    return metadata_from_attempt(
        target,
        output_dir,
        fetched_at,
        request_attempt,
        content_hash_index,
        attempt_count=2,
        fallback_error_message=rendered_attempt.error_message,
    )


def normalize_fetch_method(fetch_method: str) -> str:
    value = fetch_method.lower().strip()
    if value not in FETCH_METHODS:
        raise ValueError(f"fetch_method must be one of: {', '.join(sorted(FETCH_METHODS))}")
    return value


def fetch_via_requests(target: Target, timeout: float) -> FetchAttempt:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    try:
        response = session.get(target.url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return FetchAttempt(
            html=None,
            final_url=None,
            status_code=None,
            fetch_method="requests",
            rendered=False,
            error_message=str(exc),
        )
    return FetchAttempt(
        html=response.content.decode("utf-8", errors="replace"),
        final_url=response.url,
        status_code=response.status_code,
        fetch_method="requests",
        rendered=False,
        error_message=None,
    )


def fetch_via_playwright(target: Target, timeout: float) -> FetchAttempt:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return FetchAttempt(
            html=None,
            final_url=None,
            status_code=None,
            fetch_method="playwright",
            rendered=True,
            error_message=f"Playwright is not installed: {exc}",
        )

    timeout_ms = max(int(timeout * 1000), 1000)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="en-US",
                extra_http_headers={"Accept-Language": DEFAULT_HEADERS["Accept-Language"]},
            )
            page = context.new_page()
            response = page.goto(target.url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10000))
            except PlaywrightTimeoutError:
                pass
            try:
                page.wait_for_selector(
                    "#localTopReviewsList, #cm-cr-dp-review-list, [data-hook='review'], [data-hook='reviewContainer']",
                    timeout=min(timeout_ms, 10000),
                )
            except PlaywrightTimeoutError:
                pass
            html = page.content()
            final_url = page.url
            status_code = response.status if response else None
            context.close()
            browser.close()
    except PlaywrightError as exc:
        return FetchAttempt(
            html=None,
            final_url=None,
            status_code=None,
            fetch_method="playwright",
            rendered=True,
            error_message=str(exc),
        )

    return FetchAttempt(
        html=html,
        final_url=final_url,
        status_code=status_code,
        fetch_method="playwright",
        rendered=True,
        error_message=None,
    )


def should_keep_first_auto_attempt(attempt: FetchAttempt) -> bool:
    if not attempt.html:
        return True
    if detect_blocked_or_signin(attempt.html, attempt.status_code):
        return True
    return detect_review_section(attempt.html)


def metadata_from_attempt(
    target: Target,
    output_dir: Path,
    fetched_at: str,
    attempt: FetchAttempt,
    content_hash_index: dict[str, Path] | None,
    attempt_count: int,
    fallback_error_message: str | None = None,
) -> dict:
    if attempt.html is None:
        return {
            "target_id": target.target_id,
            "asin": target.asin,
            "requested_url": target.url,
            "final_url": attempt.final_url,
            "status": "fetch_error",
            "status_code": attempt.status_code,
            "fetched_at": fetched_at,
            "html_path": None,
            "content_hash": None,
            "blocked_or_signin": False,
            "blocked_reason": None,
            "review_section_detected": False,
            "response_bytes": 0,
            "page_title": None,
            "product_title": None,
            "error_message": attempt.error_message,
            "fallback_error_message": fallback_error_message,
            "raw_storage": None,
            "reused_from_run_id": None,
            "reused_from_html_path": None,
            "fetch_method": attempt.fetch_method,
            "rendered": attempt.rendered,
            "attempt_count": attempt_count,
        }

    html = attempt.html
    blocked_reason = detect_blocked_reason(html, attempt.status_code)
    content_hash = sha256_text(html)
    existing_html_path = content_hash_index.get(content_hash) if content_hash_index else None
    if existing_html_path and existing_html_path.exists():
        html_path = existing_html_path
        raw_storage = "deduplicated"
        deduplicated_from_html_path = str(existing_html_path)
    else:
        html_path = output_dir / f"{target.target_id}.html"
        html_path.write_text(html, encoding="utf-8")
        raw_storage = "stored"
        deduplicated_from_html_path = None
        if content_hash_index is not None:
            content_hash_index[content_hash] = html_path

    return {
        "target_id": target.target_id,
        "asin": target.asin or infer_asin_from_url(attempt.final_url or target.url),
        "requested_url": target.url,
        "final_url": attempt.final_url,
        "status": "blocked" if blocked_reason else "fetched",
        "status_code": attempt.status_code,
        "fetched_at": fetched_at,
        "html_path": str(html_path),
        "content_hash": content_hash,
        "blocked_or_signin": blocked_reason is not None,
        "blocked_reason": blocked_reason,
        "review_section_detected": detect_review_section(html),
        "response_bytes": len(html.encode("utf-8")),
        "page_title": extract_page_title(html),
        "product_title": extract_product_title(html),
        "error_message": attempt.error_message,
        "fallback_error_message": fallback_error_message,
        "raw_storage": raw_storage,
        "reused_from_run_id": None,
        "reused_from_html_path": deduplicated_from_html_path,
        "fetch_method": attempt.fetch_method,
        "rendered": attempt.rendered,
        "attempt_count": attempt_count,
    }


def detect_blocked_or_signin(html: str, status_code: int | None) -> bool:
    return detect_blocked_reason(html, status_code) is not None


def detect_blocked_reason(html: str, status_code: int | None) -> str | None:
    if status_code in {403, 429, 503}:
        return f"http_{status_code}"
    lower_html = html.lower()
    if "captcha" in lower_html or "enter the characters you see below" in lower_html:
        return "captcha"
    if 'id="ap_login_form"' in lower_html or "sign in or create account" in lower_html:
        return "sign_in"
    if "robot check" in lower_html or "automated access" in lower_html:
        return "robot_check"
    if any(marker in lower_html for marker in BLOCK_MARKERS):
        return "blocked_marker"
    return None


def extract_page_title(html: str) -> str | None:
    return extract_text(html, "title")


def extract_product_title(html: str) -> str | None:
    return extract_text(html, "#productTitle")


def extract_text(html: str, selector: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    element = soup.select_one(selector)
    if not element:
        return None
    return clean_text(element.get_text(" ", strip=True))
