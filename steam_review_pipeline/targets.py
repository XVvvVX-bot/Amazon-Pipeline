from __future__ import annotations

import csv
from pathlib import Path

from steam_review_pipeline.config import REQUIRED_TARGET_COLUMNS
from steam_review_pipeline.models import SteamApp
from steam_review_pipeline.utils import clean_text


def load_targets(path: Path) -> list[SteamApp]:
    if not path.exists():
        raise FileNotFoundError(f"Steam target file does not exist: {path}")

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED_TARGET_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Steam target file is missing columns: {', '.join(sorted(missing))}")
        return [target_from_row(row, row_number=index + 2) for index, row in enumerate(reader)]


def target_from_row(row: dict, row_number: int) -> SteamApp:
    app_id = clean_text(row.get("app_id"))
    if not app_id:
        raise ValueError(f"Row {row_number}: app_id is required")
    if not app_id.isdigit():
        raise ValueError(f"Row {row_number}: app_id must be numeric")
    return SteamApp(
        app_id=app_id,
        app_name=clean_text(row.get("app_name")),
        active=parse_bool(row.get("active"), default=True),
        notes=clean_text(row.get("notes")),
    )


def parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None or clean_text(value) == "":
        return default
    normalized = clean_text(value).lower()
    if normalized in {"1", "true", "yes", "y", "active"}:
        return True
    if normalized in {"0", "false", "no", "n", "inactive"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")
