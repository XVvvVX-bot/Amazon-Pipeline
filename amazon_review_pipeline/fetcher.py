from __future__ import annotations

from pathlib import Path

import requests
from bs4 import BeautifulSoup

from amazon_review_pipeline.config import BLOCK_MARKERS, DEFAULT_HEADERS
from amazon_review_pipeline.models import Target
from amazon_review_pipeline.targets import infer_asin_from_url
from amazon_review_pipeline.utils import clean_text, sha256_text, utc_timestamp


def fetch_target(target: Target, output_dir: Path, timeout: float, content_hash_index: dict[str, Path] | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = utc_timestamp()
    target_html_path = output_dir / f"{target.target_id}.html"
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    try:
        response = session.get(target.url, timeout=timeout, allow_redirects=True)
        html = response.content.decode("utf-8", errors="replace")
        content_hash = sha256_text(html)
        existing_html_path = content_hash_index.get(content_hash) if content_hash_index else None
        if existing_html_path and existing_html_path.exists():
            html_path = existing_html_path
            raw_storage = "deduplicated"
            deduplicated_from_html_path = str(existing_html_path)
        else:
            html_path = target_html_path
            html_path.write_text(html, encoding="utf-8")
            raw_storage = "stored"
            deduplicated_from_html_path = None
            if content_hash_index is not None:
                content_hash_index[content_hash] = html_path
        blocked_or_signin = detect_blocked_or_signin(html, response.status_code)
        return {
            "target_id": target.target_id,
            "asin": target.asin or infer_asin_from_url(response.url),
            "requested_url": target.url,
            "final_url": response.url,
            "status": "blocked" if blocked_or_signin else "fetched",
            "status_code": response.status_code,
            "fetched_at": fetched_at,
            "html_path": str(html_path),
            "content_hash": content_hash,
            "blocked_or_signin": blocked_or_signin,
            "response_bytes": len(response.content),
            "page_title": extract_page_title(html),
            "product_title": extract_product_title(html),
            "error_message": None,
            "raw_storage": raw_storage,
            "reused_from_run_id": None,
            "reused_from_html_path": deduplicated_from_html_path,
        }
    except requests.RequestException as exc:
        return {
            "target_id": target.target_id,
            "asin": target.asin,
            "requested_url": target.url,
            "final_url": None,
            "status": "fetch_error",
            "status_code": None,
            "fetched_at": fetched_at,
            "html_path": None,
            "content_hash": None,
            "blocked_or_signin": False,
            "response_bytes": 0,
            "page_title": None,
            "product_title": None,
            "error_message": str(exc),
            "raw_storage": None,
            "reused_from_run_id": None,
            "reused_from_html_path": None,
        }


def detect_blocked_or_signin(html: str, status_code: int | None) -> bool:
    if status_code in {403, 429, 503}:
        return True
    lower_html = html.lower()
    return any(marker in lower_html for marker in BLOCK_MARKERS)


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
