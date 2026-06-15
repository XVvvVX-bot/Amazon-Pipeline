from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from steam_review_pipeline.files import read_json, read_jsonl
from steam_review_pipeline.targets import load_targets
from steam_review_pipeline.utils import clean_text


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS steam_runs (
    run_id TEXT PRIMARY KEY,
    raw_dir TEXT NOT NULL,
    targets_path TEXT,
    loaded_at TEXT NOT NULL,
    app_count INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS steam_apps (
    app_id TEXT PRIMARY KEY,
    app_name TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    first_seen_run_id TEXT,
    last_seen_run_id TEXT
);

CREATE TABLE IF NOT EXISTS steam_review_pages (
    page_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    request_url TEXT,
    cursor TEXT,
    next_cursor TEXT,
    status TEXT NOT NULL,
    status_code INTEGER,
    fetched_at TEXT,
    raw_json_path TEXT,
    response_bytes INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    total_reviews INTEGER,
    total_positive INTEGER,
    total_negative INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    error_message TEXT,
    terminal_reason TEXT,
    UNIQUE (run_id, app_id, page_number),
    FOREIGN KEY (run_id) REFERENCES steam_runs(run_id),
    FOREIGN KEY (app_id) REFERENCES steam_apps(app_id)
);

CREATE TABLE IF NOT EXISTS steam_reviews (
    recommendationid TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    app_id TEXT NOT NULL,
    language TEXT,
    review TEXT,
    voted_up INTEGER NOT NULL DEFAULT 0,
    timestamp_created INTEGER,
    timestamp_updated INTEGER,
    created_at_iso TEXT,
    updated_at_iso TEXT,
    votes_up INTEGER NOT NULL DEFAULT 0,
    votes_funny INTEGER NOT NULL DEFAULT 0,
    weighted_vote_score REAL,
    comment_count INTEGER NOT NULL DEFAULT 0,
    steam_purchase INTEGER NOT NULL DEFAULT 0,
    received_for_free INTEGER NOT NULL DEFAULT 0,
    written_during_early_access INTEGER NOT NULL DEFAULT 0,
    primarily_steam_deck INTEGER NOT NULL DEFAULT 0,
    playtime_forever INTEGER,
    playtime_last_two_weeks INTEGER,
    playtime_at_review INTEGER,
    last_played INTEGER,
    collected_at TEXT,
    source_page_key TEXT,
    FOREIGN KEY (run_id) REFERENCES steam_runs(run_id),
    FOREIGN KEY (app_id) REFERENCES steam_apps(app_id),
    FOREIGN KEY (source_page_key) REFERENCES steam_review_pages(page_key)
);

CREATE INDEX IF NOT EXISTS idx_steam_reviews_app_id ON steam_reviews(app_id);
CREATE INDEX IF NOT EXISTS idx_steam_reviews_run_id ON steam_reviews(run_id);
CREATE INDEX IF NOT EXISTS idx_steam_review_pages_run_id ON steam_review_pages(run_id);
"""

EXPORT_COLUMNS = (
    "recommendationid",
    "run_id",
    "app_id",
    "app_name",
    "language",
    "review",
    "voted_up",
    "timestamp_created",
    "timestamp_updated",
    "created_at_iso",
    "updated_at_iso",
    "votes_up",
    "votes_funny",
    "weighted_vote_score",
    "comment_count",
    "steam_purchase",
    "received_for_free",
    "written_during_early_access",
    "primarily_steam_deck",
    "playtime_forever",
    "playtime_last_two_weeks",
    "playtime_at_review",
    "last_played",
    "collected_at",
    "source_page_key",
)


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def load_pipeline_run(db_path: Path, raw_dir: Path, targets_path: Path | None = None) -> dict:
    metadata_path = raw_dir / "review_pages.jsonl"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Steam review page metadata does not exist: {metadata_path}")
    page_reports = read_jsonl(metadata_path)
    run_id = raw_dir.name
    loaded_at = utc_now()
    apps_by_id = {app.app_id: app for app in load_targets(targets_path)} if targets_path and targets_path.exists() else {}
    reviews = reviews_from_page_reports(page_reports)

    with connect_database(db_path) as connection:
        initialize_database(connection)
        upsert_run(connection, run_id, raw_dir, targets_path, loaded_at, len(unique_app_ids(page_reports)), len(page_reports), len(reviews))
        app_count = upsert_apps(connection, page_reports, apps_by_id, run_id)
        page_count = upsert_pages(connection, page_reports, run_id)
        review_summary = upsert_reviews(connection, reviews, run_id)
        connection.commit()

    return {
        "db_path": str(db_path),
        "run_id": run_id,
        "raw_dir": str(raw_dir),
        "page_reports_path": str(metadata_path),
        "apps_upserted": app_count,
        "pages_upserted": page_count,
        "reviews_seen": len(reviews),
        "reviews_inserted": review_summary["inserted"],
        "reviews_updated": review_summary["updated"],
        "duplicates_skipped": review_summary["unchanged"],
    }


def reviews_from_page_reports(page_reports: list[dict]) -> list[dict]:
    reviews: list[dict] = []
    for page in page_reports:
        if page.get("status") not in {"fetched", "empty"}:
            continue
        raw_path = page.get("raw_json_path")
        if not raw_path:
            continue
        payload = read_json(Path(raw_path))
        for review in payload.get("reviews", []):
            reviews.append(normalize_review(review, page))
    return reviews


def normalize_review(review: dict, page: dict) -> dict:
    author = review.get("author") if isinstance(review.get("author"), dict) else {}
    timestamp_created = int_or_none(review.get("timestamp_created"))
    timestamp_updated = int_or_none(review.get("timestamp_updated"))
    return {
        "recommendationid": clean_text(review.get("recommendationid")),
        "run_id": page.get("run_id"),
        "app_id": clean_text(page.get("app_id")),
        "language": clean_text(review.get("language")),
        "review": clean_review_text(review.get("review")),
        "voted_up": bool(review.get("voted_up")),
        "timestamp_created": timestamp_created,
        "timestamp_updated": timestamp_updated,
        "created_at_iso": timestamp_to_iso(timestamp_created),
        "updated_at_iso": timestamp_to_iso(timestamp_updated),
        "votes_up": int_or_zero(review.get("votes_up")),
        "votes_funny": int_or_zero(review.get("votes_funny")),
        "weighted_vote_score": float_or_none(review.get("weighted_vote_score")),
        "comment_count": int_or_zero(review.get("comment_count")),
        "steam_purchase": bool(review.get("steam_purchase")),
        "received_for_free": bool(review.get("received_for_free")),
        "written_during_early_access": bool(review.get("written_during_early_access")),
        "primarily_steam_deck": bool(review.get("primarily_steam_deck")),
        "playtime_forever": int_or_none(author.get("playtime_forever")),
        "playtime_last_two_weeks": int_or_none(author.get("playtime_last_two_weeks")),
        "playtime_at_review": int_or_none(author.get("playtime_at_review")),
        "last_played": int_or_none(author.get("last_played")),
        "collected_at": clean_text(page.get("fetched_at")),
        "source_page_key": page_key(page.get("run_id") or "", page.get("app_id") or "", int(page.get("page_number") or 0)),
    }


def upsert_run(
    connection: sqlite3.Connection,
    run_id: str,
    raw_dir: Path,
    targets_path: Path | None,
    loaded_at: str,
    app_count: int,
    page_count: int,
    review_count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO steam_runs (
            run_id, raw_dir, targets_path, loaded_at, app_count, page_count, review_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            raw_dir = excluded.raw_dir,
            targets_path = excluded.targets_path,
            loaded_at = excluded.loaded_at,
            app_count = excluded.app_count,
            page_count = excluded.page_count,
            review_count = excluded.review_count
        """,
        (run_id, str(raw_dir), str(targets_path) if targets_path else None, loaded_at, app_count, page_count, review_count),
    )


def upsert_apps(connection: sqlite3.Connection, page_reports: list[dict], apps_by_id: dict, run_id: str) -> int:
    count = 0
    for app_id in unique_app_ids(page_reports):
        app = apps_by_id.get(app_id)
        page_app_name = next((clean_text(row.get("app_name")) for row in page_reports if clean_text(row.get("app_id")) == app_id), None)
        connection.execute(
            """
            INSERT INTO steam_apps (
                app_id, app_name, active, notes, first_seen_run_id, last_seen_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(app_id) DO UPDATE SET
                app_name = COALESCE(excluded.app_name, steam_apps.app_name),
                active = excluded.active,
                notes = COALESCE(excluded.notes, steam_apps.notes),
                last_seen_run_id = excluded.last_seen_run_id
            """,
            (
                app_id,
                clean_text(getattr(app, "app_name", None)) or page_app_name,
                int(bool(getattr(app, "active", True))),
                clean_text(getattr(app, "notes", None)),
                run_id,
                run_id,
            ),
        )
        count += 1
    return count


def upsert_pages(connection: sqlite3.Connection, page_reports: list[dict], run_id: str) -> int:
    count = 0
    for page in page_reports:
        connection.execute(
            """
            INSERT INTO steam_review_pages (
                page_key, run_id, app_id, page_number, request_url, cursor,
                next_cursor, status, status_code, fetched_at, raw_json_path,
                response_bytes, review_count, total_reviews, total_positive,
                total_negative, attempt_count, error_message, terminal_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, app_id, page_number) DO UPDATE SET
                request_url = excluded.request_url,
                cursor = excluded.cursor,
                next_cursor = excluded.next_cursor,
                status = excluded.status,
                status_code = excluded.status_code,
                fetched_at = excluded.fetched_at,
                raw_json_path = excluded.raw_json_path,
                response_bytes = excluded.response_bytes,
                review_count = excluded.review_count,
                total_reviews = excluded.total_reviews,
                total_positive = excluded.total_positive,
                total_negative = excluded.total_negative,
                attempt_count = excluded.attempt_count,
                error_message = excluded.error_message,
                terminal_reason = excluded.terminal_reason
            """,
            (
                page_key(run_id, page.get("app_id") or "", int(page.get("page_number") or 0)),
                run_id,
                clean_text(page.get("app_id")),
                int(page.get("page_number") or 0),
                clean_text(page.get("request_url")),
                clean_text(page.get("cursor")),
                clean_text(page.get("next_cursor")),
                clean_text(page.get("status")) or "unknown",
                page.get("status_code"),
                clean_text(page.get("fetched_at")),
                clean_text(page.get("raw_json_path")),
                int(page.get("response_bytes") or 0),
                int(page.get("review_count") or 0),
                page.get("total_reviews"),
                page.get("total_positive"),
                page.get("total_negative"),
                int(page.get("attempt_count") or 1),
                clean_text(page.get("error_message")),
                clean_text(page.get("terminal_reason")),
            ),
        )
        count += 1
    return count


def upsert_reviews(connection: sqlite3.Connection, reviews: list[dict], run_id: str) -> dict:
    inserted = 0
    updated = 0
    unchanged = 0
    for review in reviews:
        recommendationid = clean_text(review.get("recommendationid"))
        if not recommendationid:
            continue
        existing = connection.execute(
            "SELECT timestamp_updated, review FROM steam_reviews WHERE recommendationid = ?",
            (recommendationid,),
        ).fetchone()
        values = review_values(review, run_id)
        if existing is None:
            connection.execute(
                """
                INSERT INTO steam_reviews (
                    recommendationid, run_id, app_id, language, review, voted_up,
                    timestamp_created, timestamp_updated, created_at_iso, updated_at_iso,
                    votes_up, votes_funny, weighted_vote_score, comment_count,
                    steam_purchase, received_for_free, written_during_early_access,
                    primarily_steam_deck, playtime_forever, playtime_last_two_weeks,
                    playtime_at_review, last_played, collected_at, source_page_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            inserted += 1
            continue
        existing_updated = int(existing["timestamp_updated"] or 0)
        incoming_updated = int(review.get("timestamp_updated") or 0)
        if incoming_updated > existing_updated or clean_review_text(existing["review"]) != review.get("review"):
            connection.execute(
                """
                UPDATE steam_reviews
                SET run_id = ?, app_id = ?, language = ?, review = ?, voted_up = ?,
                    timestamp_created = ?, timestamp_updated = ?, created_at_iso = ?,
                    updated_at_iso = ?, votes_up = ?, votes_funny = ?,
                    weighted_vote_score = ?, comment_count = ?, steam_purchase = ?,
                    received_for_free = ?, written_during_early_access = ?,
                    primarily_steam_deck = ?, playtime_forever = ?,
                    playtime_last_two_weeks = ?, playtime_at_review = ?,
                    last_played = ?, collected_at = ?, source_page_key = ?
                WHERE recommendationid = ?
                """,
                values[1:] + (recommendationid,),
            )
            updated += 1
        else:
            unchanged += 1
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def review_values(review: dict, run_id: str) -> tuple:
    return (
        clean_text(review.get("recommendationid")),
        run_id,
        clean_text(review.get("app_id")),
        clean_text(review.get("language")),
        clean_review_text(review.get("review")),
        int(bool(review.get("voted_up"))),
        review.get("timestamp_created"),
        review.get("timestamp_updated"),
        clean_text(review.get("created_at_iso")),
        clean_text(review.get("updated_at_iso")),
        int(review.get("votes_up") or 0),
        int(review.get("votes_funny") or 0),
        review.get("weighted_vote_score"),
        int(review.get("comment_count") or 0),
        int(bool(review.get("steam_purchase"))),
        int(bool(review.get("received_for_free"))),
        int(bool(review.get("written_during_early_access"))),
        int(bool(review.get("primarily_steam_deck"))),
        review.get("playtime_forever"),
        review.get("playtime_last_two_weeks"),
        review.get("playtime_at_review"),
        review.get("last_played"),
        clean_text(review.get("collected_at")),
        clean_text(review.get("source_page_key")),
    )


def validate_database(db_path: Path, run_id: str | None = None) -> dict:
    with connect_database(db_path) as connection:
        initialize_database(connection)
        where, params = run_scope(run_id, table_alias="r")
        return {
            "db_path": str(db_path),
            "run_id": run_id,
            "counts": {
                "apps": scalar(connection, "SELECT COUNT(*) FROM steam_apps"),
                "review_pages": scoped_count(connection, "steam_review_pages", run_id),
                "reviews": scoped_count(connection, "steam_reviews", run_id),
            },
            "quality": {
                "missing_review_text": scalar(connection, f"SELECT COUNT(*) FROM steam_reviews r {where} AND (r.review IS NULL OR r.review = '')", params),
                "missing_language": scalar(connection, f"SELECT COUNT(*) FROM steam_reviews r {where} AND r.language IS NULL", params),
                "duplicate_recommendationids": 0,
            },
            "recommendation_distribution": recommendation_distribution(connection, run_id),
            "app_review_counts": app_review_counts(connection, run_id),
            "page_status_counts": page_status_counts(connection, run_id),
        }


def export_reviews(db_path: Path, output_path: Path, output_format: str, run_id: str | None = None) -> dict:
    output_format = output_format.lower()
    if output_format not in {"csv", "jsonl"}:
        raise ValueError("Export format must be 'csv' or 'jsonl'")
    with connect_database(db_path) as connection:
        initialize_database(connection)
        rows = export_review_rows(connection, run_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "csv":
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(EXPORT_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
    else:
        with output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"db_path": str(db_path), "output_path": str(output_path), "format": output_format, "run_id": run_id, "review_count": len(rows)}


def export_review_rows(connection: sqlite3.Connection, run_id: str | None = None) -> list[dict]:
    where, params = run_scope(run_id, table_alias="r")
    rows = connection.execute(
        f"""
        SELECT
            r.recommendationid, r.run_id, r.app_id, a.app_name, r.language,
            r.review, r.voted_up, r.timestamp_created, r.timestamp_updated,
            r.created_at_iso, r.updated_at_iso, r.votes_up, r.votes_funny,
            r.weighted_vote_score, r.comment_count, r.steam_purchase,
            r.received_for_free, r.written_during_early_access,
            r.primarily_steam_deck, r.playtime_forever,
            r.playtime_last_two_weeks, r.playtime_at_review, r.last_played,
            r.collected_at, r.source_page_key
        FROM steam_reviews r
        LEFT JOIN steam_apps a ON r.app_id = a.app_id
        {where}
        ORDER BY r.app_id, r.timestamp_updated DESC, r.recommendationid
        """,
        params,
    ).fetchall()
    return [normalize_export_row(dict(row)) for row in rows]


def normalize_export_row(row: dict) -> dict:
    for key in ("voted_up", "steam_purchase", "received_for_free", "written_during_early_access", "primarily_steam_deck"):
        row[key] = bool(row[key])
    return row


def unique_app_ids(page_reports: list[dict]) -> list[str]:
    return sorted({clean_text(row.get("app_id")) for row in page_reports if clean_text(row.get("app_id"))})


def page_key(run_id: str, app_id: str, page_number: int) -> str:
    return f"{run_id}:{app_id}:{page_number:04d}"


def clean_review_text(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def timestamp_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def int_or_zero(value) -> int:
    return int_or_none(value) or 0


def float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def recommendation_distribution(connection: sqlite3.Connection, run_id: str | None) -> dict:
    where, params = run_scope(run_id)
    rows = connection.execute(
        f"""
        SELECT voted_up, COUNT(*) AS count
        FROM steam_reviews
        {where}
        GROUP BY voted_up
        ORDER BY voted_up
        """,
        params,
    ).fetchall()
    return {"recommended" if row["voted_up"] else "not_recommended": int(row["count"]) for row in rows}


def app_review_counts(connection: sqlite3.Connection, run_id: str | None) -> list[dict]:
    where, params = run_scope(run_id, table_alias="r")
    rows = connection.execute(
        f"""
        SELECT r.app_id, a.app_name, COUNT(*) AS review_count
        FROM steam_reviews r
        LEFT JOIN steam_apps a ON r.app_id = a.app_id
        {where}
        GROUP BY r.app_id, a.app_name
        ORDER BY review_count DESC, r.app_id
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def page_status_counts(connection: sqlite3.Connection, run_id: str | None) -> dict[str, int]:
    where, params = run_scope(run_id)
    rows = connection.execute(
        f"""
        SELECT status, COUNT(*) AS count
        FROM steam_review_pages
        {where}
        GROUP BY status
        ORDER BY status
        """,
        params,
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}


def run_scope(run_id: str | None, table_alias: str | None = None) -> tuple[str, tuple]:
    prefix = f"{table_alias}." if table_alias else ""
    if run_id:
        return f"WHERE {prefix}run_id = ?", (run_id,)
    return "WHERE 1 = 1", ()


def scalar(connection: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    return int(connection.execute(query, params).fetchone()[0] or 0)


def scoped_count(connection: sqlite3.Connection, table: str, run_id: str | None) -> int:
    if run_id:
        return scalar(connection, f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,))
    return scalar(connection, f"SELECT COUNT(*) FROM {table}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
