from __future__ import annotations

from pathlib import Path


DEFAULT_TARGETS = Path("data/targets/steam_apps.csv")
DEFAULT_RAW_ROOT = Path("data/raw/steam")
DEFAULT_REPORTS_ROOT = Path("data/reports/steam")
DEFAULT_DB_PATH = Path("data/steam_reviews.sqlite")
DEFAULT_EXPORT_CSV = Path("data/exports/steam_reviews.csv")
REQUIRED_TARGET_COLUMNS = ("app_id", "app_name", "active", "notes")
STEAM_REVIEWS_BASE_URL = "https://store.steampowered.com/appreviews"
DEFAULT_LANGUAGE = "english"
DEFAULT_PURCHASE_TYPE = "all"
DEFAULT_REVIEW_TYPE = "all"
DEFAULT_NUM_PER_PAGE = 100
MAX_NUM_PER_PAGE = 100
VALID_REVIEW_FILTERS = {"recent", "updated", "all"}
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
