from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}_{uuid4().hex[:6]}"
