from __future__ import annotations

from pathlib import Path


DEFAULT_TARGETS = Path("data/targets/amazon_products.csv")
DEFAULT_RAW_ROOT = Path("data/raw")
DEFAULT_PARSED_ROOT = Path("data/parsed")
DEFAULT_DB_PATH = Path("data/reviews.sqlite")
REQUIRED_TARGET_COLUMNS = ("target_id", "url", "asin", "product_name", "category", "active", "notes")
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
BLOCK_MARKERS = (
    "sorry, we just need to make sure you're not a robot",
    "enter the characters you see below",
    "robot check",
    "captcha",
    "automated access",
    "sign in or create account",
    'id="ap_login_form"',
    "/ax/claim",
)
