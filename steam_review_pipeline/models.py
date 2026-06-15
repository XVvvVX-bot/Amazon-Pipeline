from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SteamApp:
    app_id: str
    app_name: str | None
    active: bool
    notes: str | None
