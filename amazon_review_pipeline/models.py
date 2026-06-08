from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    target_id: str
    url: str
    asin: str
    product_name: str | None
    category: str | None
    active: bool
    notes: str | None

