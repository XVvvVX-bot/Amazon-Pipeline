from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from steam_review_pipeline.database import (
    clean_review_text,
    normalize_export_row,
    page_key,
    review_values,
    reviews_from_page_reports,
    unique_app_ids,
    utc_now,
)
from steam_review_pipeline.files import read_jsonl
from steam_review_pipeline.targets import load_targets
from steam_review_pipeline.utils import clean_text


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS steam_runs (
    run_id TEXT PRIMARY KEY,
    raw_dir TEXT NOT NULL,
    targets_path TEXT,
    loaded_at TEXT NOT NULL,
    app_count INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    reviews_inserted INTEGER NOT NULL DEFAULT 0,
    reviews_updated INTEGER NOT NULL DEFAULT 0,
    duplicates_skipped INTEGER NOT NULL DEFAULT 0,
    fetch_errors INTEGER NOT NULL DEFAULT 0,
    rate_limited_pages INTEGER NOT NULL DEFAULT 0
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
    run_id TEXT NOT NULL REFERENCES steam_runs(run_id),
    app_id TEXT NOT NULL REFERENCES steam_apps(app_id),
    page_number INTEGER NOT NULL,
    request_url TEXT,
    cursor TEXT,
    next_cursor TEXT,
    status TEXT NOT NULL,
    status_code INTEGER,
    fetched_at TEXT,
    raw_json_path TEXT,
    response_bytes BIGINT NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    total_reviews BIGINT,
    total_positive BIGINT,
    total_negative BIGINT,
    max_timestamp_updated BIGINT,
    min_timestamp_updated BIGINT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    error_message TEXT,
    terminal_reason TEXT,
    UNIQUE (run_id, app_id, page_number)
);

CREATE TABLE IF NOT EXISTS steam_reviews (
    recommendationid TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES steam_runs(run_id),
    app_id TEXT NOT NULL REFERENCES steam_apps(app_id),
    language TEXT,
    review TEXT,
    voted_up INTEGER NOT NULL DEFAULT 0,
    timestamp_created BIGINT,
    timestamp_updated BIGINT,
    created_at_iso TEXT,
    updated_at_iso TEXT,
    votes_up BIGINT NOT NULL DEFAULT 0,
    votes_funny BIGINT NOT NULL DEFAULT 0,
    weighted_vote_score DOUBLE PRECISION,
    comment_count BIGINT NOT NULL DEFAULT 0,
    steam_purchase INTEGER NOT NULL DEFAULT 0,
    received_for_free INTEGER NOT NULL DEFAULT 0,
    written_during_early_access INTEGER NOT NULL DEFAULT 0,
    primarily_steam_deck INTEGER NOT NULL DEFAULT 0,
    playtime_forever BIGINT,
    playtime_last_two_weeks BIGINT,
    playtime_at_review BIGINT,
    last_played BIGINT,
    collected_at TEXT,
    source_page_key TEXT REFERENCES steam_review_pages(page_key)
);

CREATE TABLE IF NOT EXISTS steam_review_changes (
    change_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES steam_runs(run_id),
    recommendationid TEXT NOT NULL REFERENCES steam_reviews(recommendationid),
    app_id TEXT NOT NULL REFERENCES steam_apps(app_id),
    change_type TEXT NOT NULL CHECK (change_type IN ('inserted', 'updated')),
    previous_timestamp_updated BIGINT,
    new_timestamp_updated BIGINT,
    source_page_key TEXT REFERENCES steam_review_pages(page_key),
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, recommendationid)
);

CREATE TABLE IF NOT EXISTS steam_app_sync_state (
    app_id TEXT PRIMARY KEY REFERENCES steam_apps(app_id),
    complete_through_timestamp_updated BIGINT NOT NULL DEFAULT 0,
    backlogged INTEGER NOT NULL DEFAULT 1,
    last_started_at TEXT,
    last_completed_at TEXT,
    last_run_id TEXT,
    last_successful_run_id TEXT,
    last_terminal_reason TEXT,
    last_seen_max_timestamp_updated BIGINT,
    last_seen_min_timestamp_updated BIGINT,
    last_page_count INTEGER NOT NULL DEFAULT 0,
    last_review_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE steam_review_pages ADD COLUMN IF NOT EXISTS max_timestamp_updated BIGINT;
ALTER TABLE steam_review_pages ADD COLUMN IF NOT EXISTS min_timestamp_updated BIGINT;

ALTER TABLE steam_review_pages
    ALTER COLUMN response_bytes TYPE BIGINT,
    ALTER COLUMN total_reviews TYPE BIGINT,
    ALTER COLUMN total_positive TYPE BIGINT,
    ALTER COLUMN total_negative TYPE BIGINT,
    ALTER COLUMN max_timestamp_updated TYPE BIGINT,
    ALTER COLUMN min_timestamp_updated TYPE BIGINT;

ALTER TABLE steam_reviews
    ALTER COLUMN timestamp_created TYPE BIGINT,
    ALTER COLUMN timestamp_updated TYPE BIGINT,
    ALTER COLUMN votes_up TYPE BIGINT,
    ALTER COLUMN votes_funny TYPE BIGINT,
    ALTER COLUMN comment_count TYPE BIGINT,
    ALTER COLUMN playtime_forever TYPE BIGINT,
    ALTER COLUMN playtime_last_two_weeks TYPE BIGINT,
    ALTER COLUMN playtime_at_review TYPE BIGINT,
    ALTER COLUMN last_played TYPE BIGINT;

ALTER TABLE steam_review_changes
    ALTER COLUMN previous_timestamp_updated TYPE BIGINT,
    ALTER COLUMN new_timestamp_updated TYPE BIGINT;

ALTER TABLE steam_app_sync_state
    ALTER COLUMN complete_through_timestamp_updated TYPE BIGINT,
    ALTER COLUMN last_seen_max_timestamp_updated TYPE BIGINT,
    ALTER COLUMN last_seen_min_timestamp_updated TYPE BIGINT;

CREATE INDEX IF NOT EXISTS idx_steam_reviews_app_id ON steam_reviews(app_id);
CREATE INDEX IF NOT EXISTS idx_steam_reviews_run_id ON steam_reviews(run_id);
CREATE INDEX IF NOT EXISTS idx_steam_reviews_app_updated ON steam_reviews(app_id, timestamp_updated DESC);
CREATE INDEX IF NOT EXISTS idx_steam_review_pages_run_id ON steam_review_pages(run_id);
CREATE INDEX IF NOT EXISTS idx_steam_review_changes_run_id ON steam_review_changes(run_id);
CREATE INDEX IF NOT EXISTS idx_steam_app_sync_state_backlogged ON steam_app_sync_state(backlogged);
"""

COMPLETE_TERMINAL_REASONS = {"caught_up_to_existing_reviews", "empty_page", "missing_next_cursor"}
SYNCED_FILTERS = {"recent", "updated"}
REVIEW_UPSERT_BATCH_SIZE = 5000


def connect_postgres(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


def initialize_postgres(database_url: str) -> None:
    with connect_postgres(database_url) as connection:
        connection.execute(POSTGRES_SCHEMA)
        connection.commit()


def app_high_water_marks(database_url: str, app_ids: Iterable[str]) -> dict[str, int]:
    app_id_list = [str(app_id) for app_id in app_ids]
    if not app_id_list:
        return {}
    with connect_postgres(database_url) as connection:
        connection.execute(POSTGRES_SCHEMA)
        rows = connection.execute(
            """
            SELECT app_id, COALESCE(complete_through_timestamp_updated, 0) AS high_water
            FROM steam_app_sync_state
            WHERE app_id = ANY(%s)
            """,
            (app_id_list,),
        ).fetchall()
    marks = {app_id: 0 for app_id in app_id_list}
    marks.update({str(row["app_id"]): int(row["high_water"] or 0) for row in rows})
    return marks


def app_sync_states(database_url: str, app_ids: Iterable[str] | None = None) -> dict[str, dict]:
    app_id_list = [str(app_id) for app_id in app_ids] if app_ids is not None else []
    with connect_postgres(database_url) as connection:
        connection.execute(POSTGRES_SCHEMA)
        if app_ids is None:
            rows = connection.execute(
                """
                SELECT app_id, complete_through_timestamp_updated, backlogged,
                    last_started_at, last_completed_at, last_run_id,
                    last_successful_run_id, last_terminal_reason,
                    last_seen_max_timestamp_updated, last_seen_min_timestamp_updated,
                    last_page_count, last_review_count, updated_at
                FROM steam_app_sync_state
                ORDER BY app_id
                """
            ).fetchall()
        elif app_id_list:
            rows = connection.execute(
                """
                SELECT app_id, complete_through_timestamp_updated, backlogged,
                    last_started_at, last_completed_at, last_run_id,
                    last_successful_run_id, last_terminal_reason,
                    last_seen_max_timestamp_updated, last_seen_min_timestamp_updated,
                    last_page_count, last_review_count, updated_at
                FROM steam_app_sync_state
                WHERE app_id = ANY(%s)
                ORDER BY app_id
                """,
                (app_id_list,),
            ).fetchall()
        else:
            rows = []
    states = {str(row["app_id"]): normalize_sync_state_row(row) for row in rows}
    for app_id in app_id_list:
        states.setdefault(app_id, default_sync_state(app_id))
    return states


def default_sync_state(app_id: str) -> dict:
    return {
        "app_id": str(app_id),
        "complete_through_timestamp_updated": 0,
        "backlogged": True,
        "last_started_at": None,
        "last_completed_at": None,
        "last_run_id": None,
        "last_successful_run_id": None,
        "last_terminal_reason": None,
        "last_seen_max_timestamp_updated": None,
        "last_seen_min_timestamp_updated": None,
        "last_page_count": 0,
        "last_review_count": 0,
        "updated_at": None,
    }


def normalize_sync_state_row(row: dict) -> dict:
    state = dict(row)
    state["backlogged"] = bool(state.get("backlogged"))
    state["complete_through_timestamp_updated"] = int(state.get("complete_through_timestamp_updated") or 0)
    state["last_page_count"] = int(state.get("last_page_count") or 0)
    state["last_review_count"] = int(state.get("last_review_count") or 0)
    return state


def update_app_sync_states(
    database_url: str,
    page_reports: list[dict],
    run_id: str,
    review_filter: str,
    started_at: str,
    completed_at: str,
) -> dict:
    if review_filter not in SYNCED_FILTERS:
        return {
            "states_updated": 0,
            "complete_apps": [],
            "backlogged_apps": [],
            "skipped": True,
            "reason": f"filter={review_filter} is not chronological",
        }

    app_reports: dict[str, list[dict]] = {}
    for row in page_reports:
        app_id = clean_text(row.get("app_id"))
        if app_id:
            app_reports.setdefault(app_id, []).append(row)

    complete_apps: list[str] = []
    backlogged_apps: list[str] = []
    terminal_reasons: dict[str, int] = {}
    watermarks: dict[str, int] = {}

    with connect_postgres(database_url) as connection:
        connection.execute(POSTGRES_SCHEMA)
        for app_id, rows in sorted(app_reports.items()):
            last_row = rows[-1]
            terminal_reason = clean_text(last_row.get("terminal_reason")) or "unknown"
            terminal_reasons[terminal_reason] = terminal_reasons.get(terminal_reason, 0) + 1
            complete = terminal_reason in COMPLETE_TERMINAL_REASONS
            previous = connection.execute(
                """
                SELECT complete_through_timestamp_updated, last_successful_run_id
                FROM steam_app_sync_state
                WHERE app_id = %s
                """,
                (app_id,),
            ).fetchone()
            previous_watermark = int((previous or {}).get("complete_through_timestamp_updated") or 0)
            previous_successful_run_id = (previous or {}).get("last_successful_run_id")
            max_seen = max_page_value(rows, "max_timestamp_updated")
            min_seen = min_page_value(rows, "min_timestamp_updated")
            new_watermark = max(previous_watermark, max_seen or 0) if complete else previous_watermark
            last_successful_run_id = run_id if complete else previous_successful_run_id
            backlogged = not complete
            upsert_app_sync_state(
                connection,
                app_id=app_id,
                complete_through_timestamp_updated=new_watermark,
                backlogged=backlogged,
                started_at=started_at,
                completed_at=completed_at,
                run_id=run_id,
                last_successful_run_id=last_successful_run_id,
                terminal_reason=terminal_reason,
                max_seen=max_seen,
                min_seen=min_seen,
                page_count=len(rows),
                review_count=sum(int(row.get("review_count") or 0) for row in rows),
            )
            watermarks[app_id] = new_watermark
            if complete:
                complete_apps.append(app_id)
            else:
                backlogged_apps.append(app_id)
        connection.commit()

    return {
        "states_updated": len(app_reports),
        "complete_apps": complete_apps,
        "backlogged_apps": backlogged_apps,
        "terminal_reasons": terminal_reasons,
        "watermarks": watermarks,
    }


def upsert_app_sync_state(
    connection: psycopg.Connection,
    app_id: str,
    complete_through_timestamp_updated: int,
    backlogged: bool,
    started_at: str,
    completed_at: str,
    run_id: str,
    last_successful_run_id: str | None,
    terminal_reason: str,
    max_seen: int | None,
    min_seen: int | None,
    page_count: int,
    review_count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO steam_app_sync_state (
            app_id, complete_through_timestamp_updated, backlogged,
            last_started_at, last_completed_at, last_run_id,
            last_successful_run_id, last_terminal_reason,
            last_seen_max_timestamp_updated, last_seen_min_timestamp_updated,
            last_page_count, last_review_count, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT(app_id) DO UPDATE SET
            complete_through_timestamp_updated = EXCLUDED.complete_through_timestamp_updated,
            backlogged = EXCLUDED.backlogged,
            last_started_at = EXCLUDED.last_started_at,
            last_completed_at = EXCLUDED.last_completed_at,
            last_run_id = EXCLUDED.last_run_id,
            last_successful_run_id = EXCLUDED.last_successful_run_id,
            last_terminal_reason = EXCLUDED.last_terminal_reason,
            last_seen_max_timestamp_updated = EXCLUDED.last_seen_max_timestamp_updated,
            last_seen_min_timestamp_updated = EXCLUDED.last_seen_min_timestamp_updated,
            last_page_count = EXCLUDED.last_page_count,
            last_review_count = EXCLUDED.last_review_count,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            app_id,
            complete_through_timestamp_updated,
            int(backlogged),
            started_at,
            completed_at,
            run_id,
            last_successful_run_id,
            terminal_reason,
            max_seen,
            min_seen,
            page_count,
            review_count,
        ),
    )


def max_page_value(rows: list[dict], field: str) -> int | None:
    values = [int(row[field]) for row in rows if row.get(field) is not None]
    return max(values) if values else None


def min_page_value(rows: list[dict], field: str) -> int | None:
    values = [int(row[field]) for row in rows if row.get(field) is not None]
    return min(values) if values else None


def load_pipeline_run_postgres(database_url: str, raw_dir: Path, targets_path: Path | None = None) -> dict:
    metadata_path = raw_dir / "review_pages.jsonl"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Steam review page metadata does not exist: {metadata_path}")
    page_reports = read_jsonl(metadata_path)
    run_id = raw_dir.name
    loaded_at = utc_now()
    apps_by_id = {app.app_id: app for app in load_targets(targets_path)} if targets_path and targets_path.exists() else {}
    reviews = reviews_from_page_reports(page_reports, raw_dir)

    with connect_postgres(database_url) as connection:
        connection.execute(POSTGRES_SCHEMA)
        upsert_run_postgres(
            connection,
            run_id,
            raw_dir,
            targets_path,
            loaded_at,
            len(unique_app_ids(page_reports)),
            len(page_reports),
            len(reviews),
            fetch_errors=sum(1 for row in page_reports if row.get("status") == "fetch_error"),
            rate_limited_pages=sum(1 for row in page_reports if row.get("status_code") == 429),
        )
        app_count = upsert_apps_postgres(connection, page_reports, apps_by_id, run_id)
        page_count = upsert_pages_postgres(connection, page_reports, run_id)
        review_summary = upsert_reviews_postgres(connection, reviews, run_id)
        upsert_run_postgres(
            connection,
            run_id,
            raw_dir,
            targets_path,
            loaded_at,
            len(unique_app_ids(page_reports)),
            len(page_reports),
            len(reviews),
            reviews_inserted=review_summary["inserted"],
            reviews_updated=review_summary["updated"],
            duplicates_skipped=review_summary["unchanged"],
            fetch_errors=sum(1 for row in page_reports if row.get("status") == "fetch_error"),
            rate_limited_pages=sum(1 for row in page_reports if row.get("status_code") == 429),
        )
        connection.commit()

    return {
        "database_url": mask_database_url(database_url),
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


def upsert_run_postgres(
    connection: psycopg.Connection,
    run_id: str,
    raw_dir: Path,
    targets_path: Path | None,
    loaded_at: str,
    app_count: int,
    page_count: int,
    review_count: int,
    reviews_inserted: int = 0,
    reviews_updated: int = 0,
    duplicates_skipped: int = 0,
    fetch_errors: int = 0,
    rate_limited_pages: int = 0,
) -> None:
    connection.execute(
        """
        INSERT INTO steam_runs (
            run_id, raw_dir, targets_path, loaded_at, app_count, page_count,
            review_count, reviews_inserted, reviews_updated, duplicates_skipped,
            fetch_errors, rate_limited_pages
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(run_id) DO UPDATE SET
            raw_dir = EXCLUDED.raw_dir,
            targets_path = EXCLUDED.targets_path,
            loaded_at = EXCLUDED.loaded_at,
            app_count = EXCLUDED.app_count,
            page_count = EXCLUDED.page_count,
            review_count = EXCLUDED.review_count,
            reviews_inserted = EXCLUDED.reviews_inserted,
            reviews_updated = EXCLUDED.reviews_updated,
            duplicates_skipped = EXCLUDED.duplicates_skipped,
            fetch_errors = EXCLUDED.fetch_errors,
            rate_limited_pages = EXCLUDED.rate_limited_pages
        """,
        (
            run_id,
            str(raw_dir),
            str(targets_path) if targets_path else None,
            loaded_at,
            app_count,
            page_count,
            review_count,
            reviews_inserted,
            reviews_updated,
            duplicates_skipped,
            fetch_errors,
            rate_limited_pages,
        ),
    )


def upsert_apps_postgres(connection: psycopg.Connection, page_reports: list[dict], apps_by_id: dict, run_id: str) -> int:
    count = 0
    for app_id in unique_app_ids(page_reports):
        app = apps_by_id.get(app_id)
        page_app_name = next((clean_text(row.get("app_name")) for row in page_reports if clean_text(row.get("app_id")) == app_id), None)
        connection.execute(
            """
            INSERT INTO steam_apps (
                app_id, app_name, active, notes, first_seen_run_id, last_seen_run_id
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(app_id) DO UPDATE SET
                app_name = COALESCE(EXCLUDED.app_name, steam_apps.app_name),
                active = EXCLUDED.active,
                notes = COALESCE(EXCLUDED.notes, steam_apps.notes),
                last_seen_run_id = EXCLUDED.last_seen_run_id
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


def upsert_pages_postgres(connection: psycopg.Connection, page_reports: list[dict], run_id: str) -> int:
    count = 0
    for page in page_reports:
        connection.execute(
            """
            INSERT INTO steam_review_pages (
                page_key, run_id, app_id, page_number, request_url, cursor,
                next_cursor, status, status_code, fetched_at, raw_json_path,
                response_bytes, review_count, total_reviews, total_positive,
                total_negative, max_timestamp_updated, min_timestamp_updated,
                attempt_count, error_message, terminal_reason
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(run_id, app_id, page_number) DO UPDATE SET
                request_url = EXCLUDED.request_url,
                cursor = EXCLUDED.cursor,
                next_cursor = EXCLUDED.next_cursor,
                status = EXCLUDED.status,
                status_code = EXCLUDED.status_code,
                fetched_at = EXCLUDED.fetched_at,
                raw_json_path = EXCLUDED.raw_json_path,
                response_bytes = EXCLUDED.response_bytes,
                review_count = EXCLUDED.review_count,
                total_reviews = EXCLUDED.total_reviews,
                total_positive = EXCLUDED.total_positive,
                total_negative = EXCLUDED.total_negative,
                max_timestamp_updated = EXCLUDED.max_timestamp_updated,
                min_timestamp_updated = EXCLUDED.min_timestamp_updated,
                attempt_count = EXCLUDED.attempt_count,
                error_message = EXCLUDED.error_message,
                terminal_reason = EXCLUDED.terminal_reason
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
                page.get("max_timestamp_updated"),
                page.get("min_timestamp_updated"),
                int(page.get("attempt_count") or 1),
                clean_text(page.get("error_message")),
                clean_text(page.get("terminal_reason")),
            ),
        )
        count += 1
    return count


def upsert_reviews_postgres(connection: psycopg.Connection, reviews: list[dict], run_id: str) -> dict:
    inserted = 0
    updated = 0
    unchanged = 0
    for batch in chunks(reviews, REVIEW_UPSERT_BATCH_SIZE):
        recommendationids = [clean_text(review.get("recommendationid")) for review in batch if clean_text(review.get("recommendationid"))]
        if not recommendationids:
            continue
        existing_rows = connection.execute(
            """
            SELECT recommendationid, timestamp_updated, review
            FROM steam_reviews
            WHERE recommendationid = ANY(%s)
            """,
            (recommendationids,),
        ).fetchall()
        existing_by_id = {
            clean_text(row["recommendationid"]): {
                "timestamp_updated": int(row["timestamp_updated"] or 0),
                "review": clean_review_text(row["review"]),
            }
            for row in existing_rows
        }
        insert_values = []
        update_values = []
        change_values = []
        for review in batch:
            recommendationid = clean_text(review.get("recommendationid"))
            if not recommendationid:
                continue
            values = review_values(review, run_id)
            existing = existing_by_id.get(recommendationid)
            incoming_updated = int(review.get("timestamp_updated") or 0)
            incoming_review = review.get("review")
            if existing is None:
                insert_values.append(values)
                change_values.append(change_values_tuple(run_id, review, "inserted", None))
                existing_by_id[recommendationid] = {"timestamp_updated": incoming_updated, "review": incoming_review}
                inserted += 1
                continue
            existing_updated = int(existing.get("timestamp_updated") or 0)
            if incoming_updated > existing_updated or clean_review_text(existing.get("review")) != incoming_review:
                update_values.append(values[1:] + (recommendationid,))
                change_values.append(change_values_tuple(run_id, review, "updated", existing_updated))
                existing_by_id[recommendationid] = {"timestamp_updated": incoming_updated, "review": incoming_review}
                updated += 1
            else:
                unchanged += 1
        with connection.cursor() as cursor:
            if insert_values:
                cursor.executemany(INSERT_REVIEW_SQL, insert_values)
            if update_values:
                cursor.executemany(UPDATE_REVIEW_SQL, update_values)
            if change_values:
                cursor.executemany(UPSERT_REVIEW_CHANGE_SQL, change_values)
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


INSERT_REVIEW_SQL = """
INSERT INTO steam_reviews (
    recommendationid, run_id, app_id, language, review, voted_up,
    timestamp_created, timestamp_updated, created_at_iso, updated_at_iso,
    votes_up, votes_funny, weighted_vote_score, comment_count,
    steam_purchase, received_for_free, written_during_early_access,
    primarily_steam_deck, playtime_forever, playtime_last_two_weeks,
    playtime_at_review, last_played, collected_at, source_page_key
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(recommendationid) DO NOTHING
"""


UPDATE_REVIEW_SQL = """
UPDATE steam_reviews
SET run_id = %s, app_id = %s, language = %s, review = %s, voted_up = %s,
    timestamp_created = %s, timestamp_updated = %s, created_at_iso = %s,
    updated_at_iso = %s, votes_up = %s, votes_funny = %s,
    weighted_vote_score = %s, comment_count = %s, steam_purchase = %s,
    received_for_free = %s, written_during_early_access = %s,
    primarily_steam_deck = %s, playtime_forever = %s,
    playtime_last_two_weeks = %s, playtime_at_review = %s,
    last_played = %s, collected_at = %s, source_page_key = %s
WHERE recommendationid = %s
"""


UPSERT_REVIEW_CHANGE_SQL = """
INSERT INTO steam_review_changes (
    run_id, recommendationid, app_id, change_type,
    previous_timestamp_updated, new_timestamp_updated, source_page_key
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(run_id, recommendationid) DO UPDATE SET
    change_type = EXCLUDED.change_type,
    previous_timestamp_updated = EXCLUDED.previous_timestamp_updated,
    new_timestamp_updated = EXCLUDED.new_timestamp_updated,
    source_page_key = EXCLUDED.source_page_key,
    changed_at = CURRENT_TIMESTAMP
"""


def chunks(items: list[dict], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def change_values_tuple(run_id: str, review: dict, change_type: str, previous_updated: int | None) -> tuple:
    return (
        run_id,
        clean_text(review.get("recommendationid")),
        clean_text(review.get("app_id")),
        change_type,
        previous_updated,
        review.get("timestamp_updated"),
        clean_text(review.get("source_page_key")),
    )


def insert_change(connection: psycopg.Connection, run_id: str, review: dict, change_type: str, previous_updated: int | None) -> None:
    connection.execute(UPSERT_REVIEW_CHANGE_SQL, change_values_tuple(run_id, review, change_type, previous_updated))


def validate_postgres(database_url: str, run_id: str | None = None) -> dict:
    with connect_postgres(database_url) as connection:
        connection.execute(POSTGRES_SCHEMA)
        where, params = postgres_run_scope(run_id, table_alias="r")
        return {
            "database_url": mask_database_url(database_url),
            "run_id": run_id,
            "counts": {
                "apps": scalar_postgres(connection, "SELECT COUNT(*) FROM steam_apps"),
                "review_pages": scoped_count_postgres(connection, "steam_review_pages", run_id),
                "reviews": scoped_count_postgres(connection, "steam_reviews", run_id),
                "review_changes": scoped_count_postgres(connection, "steam_review_changes", run_id),
                "sync_states": scalar_postgres(connection, "SELECT COUNT(*) FROM steam_app_sync_state"),
            },
            "quality": {
                "missing_review_text": scalar_postgres(connection, f"SELECT COUNT(*) FROM steam_reviews r {where} AND (r.review IS NULL OR r.review = '')", params),
                "missing_language": scalar_postgres(connection, f"SELECT COUNT(*) FROM steam_reviews r {where} AND r.language IS NULL", params),
                "duplicate_recommendationids": 0,
            },
            "recommendation_distribution": recommendation_distribution_postgres(connection, run_id),
            "app_review_counts": app_review_counts_postgres(connection, run_id),
            "page_status_counts": page_status_counts_postgres(connection, run_id),
            "change_counts": change_counts_postgres(connection, run_id),
            "sync_state": app_sync_state_summary_postgres(connection),
        }


def export_reviews_postgres(database_url: str, output_path: Path, output_format: str, run_id: str | None = None) -> dict:
    from steam_review_pipeline.database import EXPORT_COLUMNS
    import csv
    import json

    output_format = output_format.lower()
    if output_format not in {"csv", "jsonl"}:
        raise ValueError("Export format must be 'csv' or 'jsonl'")
    with connect_postgres(database_url) as connection:
        connection.execute(POSTGRES_SCHEMA)
        rows = export_review_rows_postgres(connection, run_id)
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
    return {"database_url": mask_database_url(database_url), "output_path": str(output_path), "format": output_format, "run_id": run_id, "review_count": len(rows)}


def export_review_rows_postgres(connection: psycopg.Connection, run_id: str | None = None) -> list[dict]:
    where, params = postgres_run_scope(run_id, table_alias="r")
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


def migrate_sqlite_to_postgres(sqlite_path: Path, database_url: str, batch_size: int = 5000) -> dict:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database does not exist: {sqlite_path}")
    with sqlite3.connect(sqlite_path) as sqlite_connection, connect_postgres(database_url) as postgres_connection:
        sqlite_connection.row_factory = sqlite3.Row
        postgres_connection.execute(POSTGRES_SCHEMA)
        run_count = copy_table(sqlite_connection, postgres_connection, "steam_runs", STEAM_RUN_COLUMNS, batch_size)
        app_count = copy_table(sqlite_connection, postgres_connection, "steam_apps", STEAM_APP_COLUMNS, batch_size)
        page_count = copy_table(sqlite_connection, postgres_connection, "steam_review_pages", STEAM_REVIEW_PAGE_COLUMNS, batch_size)
        review_count = copy_table(sqlite_connection, postgres_connection, "steam_reviews", STEAM_REVIEW_COLUMNS, batch_size)
        postgres_connection.commit()
    return {
        "sqlite_path": str(sqlite_path),
        "database_url": mask_database_url(database_url),
        "runs": run_count,
        "apps": app_count,
        "review_pages": page_count,
        "reviews": review_count,
    }


STEAM_RUN_COLUMNS = (
    "run_id",
    "raw_dir",
    "targets_path",
    "loaded_at",
    "app_count",
    "page_count",
    "review_count",
)
STEAM_APP_COLUMNS = (
    "app_id",
    "app_name",
    "active",
    "notes",
    "first_seen_run_id",
    "last_seen_run_id",
)
STEAM_REVIEW_PAGE_COLUMNS = (
    "page_key",
    "run_id",
    "app_id",
    "page_number",
    "request_url",
    "cursor",
    "next_cursor",
    "status",
    "status_code",
    "fetched_at",
    "raw_json_path",
    "response_bytes",
    "review_count",
    "total_reviews",
    "total_positive",
    "total_negative",
    "attempt_count",
    "error_message",
    "terminal_reason",
)
STEAM_REVIEW_COLUMNS = (
    "recommendationid",
    "run_id",
    "app_id",
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


def copy_table(sqlite_connection: sqlite3.Connection, postgres_connection: psycopg.Connection, table: str, columns: tuple[str, ...], batch_size: int) -> int:
    existing = sqlite_connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    if not existing:
        return 0
    selected_columns = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    conflict_target = columns[0]
    update_columns = [column for column in columns if column != conflict_target]
    update_sql = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
    insert_sql = f"""
        INSERT INTO {table} ({selected_columns})
        VALUES ({placeholders})
        ON CONFLICT({conflict_target}) DO UPDATE SET {update_sql}
    """
    total = 0
    cursor = sqlite_connection.execute(f"SELECT {selected_columns} FROM {table}")
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        values = [tuple(row[column] for column in columns) for row in rows]
        postgres_connection.cursor().executemany(insert_sql, values)
        total += len(values)
    return total


def postgres_run_scope(run_id: str | None, table_alias: str | None = None) -> tuple[str, tuple]:
    prefix = f"{table_alias}." if table_alias else ""
    if run_id:
        return f"WHERE {prefix}run_id = %s", (run_id,)
    return "WHERE 1 = 1", ()


def scalar_postgres(connection: psycopg.Connection, query: str, params: tuple = ()) -> int:
    return int(connection.execute(query, params).fetchone()["count"] or 0)


def scoped_count_postgres(connection: psycopg.Connection, table: str, run_id: str | None) -> int:
    if run_id:
        return scalar_postgres(connection, f"SELECT COUNT(*) AS count FROM {table} WHERE run_id = %s", (run_id,))
    return scalar_postgres(connection, f"SELECT COUNT(*) AS count FROM {table}")


def recommendation_distribution_postgres(connection: psycopg.Connection, run_id: str | None) -> dict:
    where, params = postgres_run_scope(run_id)
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


def app_review_counts_postgres(connection: psycopg.Connection, run_id: str | None) -> list[dict]:
    where, params = postgres_run_scope(run_id, table_alias="r")
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


def page_status_counts_postgres(connection: psycopg.Connection, run_id: str | None) -> dict[str, int]:
    where, params = postgres_run_scope(run_id)
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


def change_counts_postgres(connection: psycopg.Connection, run_id: str | None) -> dict[str, int]:
    where, params = postgres_run_scope(run_id)
    rows = connection.execute(
        f"""
        SELECT change_type, COUNT(*) AS count
        FROM steam_review_changes
        {where}
        GROUP BY change_type
        ORDER BY change_type
        """,
        params,
    ).fetchall()
    return {str(row["change_type"]): int(row["count"]) for row in rows}


def app_sync_state_summary_postgres(connection: psycopg.Connection) -> dict:
    rows = connection.execute(
        """
        SELECT app_id, complete_through_timestamp_updated, backlogged,
            last_terminal_reason, last_run_id, last_successful_run_id,
            last_seen_max_timestamp_updated, last_page_count, last_review_count
        FROM steam_app_sync_state
        ORDER BY backlogged DESC, app_id
        """
    ).fetchall()
    states = [normalize_sync_state_row(row) for row in rows]
    return {
        "state_count": len(states),
        "backlogged_count": sum(1 for row in states if row["backlogged"]),
        "complete_count": sum(1 for row in states if not row["backlogged"]),
        "apps": states,
    }


def mask_database_url(database_url: str) -> str:
    if "@" not in database_url or "://" not in database_url:
        return database_url
    scheme, rest = database_url.split("://", 1)
    credentials, host = rest.rsplit("@", 1)
    if ":" not in credentials:
        return f"{scheme}://***@{host}"
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
