from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urlparse

from amazon_review_pipeline.config import REQUIRED_TARGET_COLUMNS
from amazon_review_pipeline.models import Target
from amazon_review_pipeline.utils import clean_text


def load_targets(path: Path) -> list[Target]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing_columns = set(REQUIRED_TARGET_COLUMNS).difference(reader.fieldnames or [])
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Target CSV is missing required columns: {missing}")

        targets: list[Target] = []
        seen_ids: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            target_id = clean_text(row.get("target_id"))
            url = clean_text(row.get("url"))
            if not target_id:
                raise ValueError(f"Line {line_number}: target_id is required")
            if target_id in seen_ids:
                raise ValueError(f"Line {line_number}: duplicate target_id {target_id!r}")
            if not url:
                raise ValueError(f"Line {line_number}: url is required")

            asin = clean_text(row.get("asin")) or infer_asin_from_url(url) or ""
            targets.append(
                Target(
                    target_id=target_id,
                    url=url,
                    asin=asin,
                    product_name=clean_text(row.get("product_name")),
                    category=clean_text(row.get("category")),
                    active=parse_bool(row.get("active"), line_number),
                    notes=clean_text(row.get("notes")),
                )
            )
            seen_ids.add(target_id)
    return targets


def parse_bool(value: str | None, line_number: int) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"true", "t", "yes", "y", "1"}:
        return True
    if normalized in {"false", "f", "no", "n", "0"}:
        return False
    raise ValueError(f"Line {line_number}: active must be true or false")


def infer_asin_from_url(url: str) -> str | None:
    path = urlparse(url).path
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)", path, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None

