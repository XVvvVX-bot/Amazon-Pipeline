from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from amazon_review_pipeline.extraction import extract_reviews_from_raw_dir
from amazon_review_pipeline.files import infer_run_id, load_fetch_metadata
from amazon_review_pipeline.targets import load_targets
from amazon_review_pipeline.utils import clean_text, sha256_text


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    raw_dir TEXT,
    parsed_dir TEXT,
    targets_path TEXT,
    loaded_at TEXT NOT NULL,
    target_count INTEGER NOT NULL DEFAULT 0,
    raw_page_count INTEGER NOT NULL DEFAULT 0,
    parsed_review_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
    target_id TEXT PRIMARY KEY,
    asin TEXT,
    product_name TEXT,
    category TEXT,
    source_url TEXT,
    first_seen_run_id TEXT,
    last_seen_run_id TEXT
);

CREATE TABLE IF NOT EXISTS raw_pages (
    page_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    asin TEXT,
    requested_url TEXT,
    final_url TEXT,
    status TEXT,
    status_code INTEGER,
    fetched_at TEXT,
    html_path TEXT,
    raw_storage TEXT NOT NULL DEFAULT 'stored',
    reused_from_run_id TEXT,
    reused_from_html_path TEXT,
    content_hash TEXT,
    blocked_or_signin INTEGER NOT NULL DEFAULT 0,
    blocked_reason TEXT,
    review_section_detected INTEGER NOT NULL DEFAULT 0,
    response_bytes INTEGER NOT NULL DEFAULT 0,
    page_title TEXT,
    product_title TEXT,
    error_message TEXT,
    fallback_error_message TEXT,
    fetch_method TEXT,
    rendered INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE (run_id, target_id),
    FOREIGN KEY (run_id) REFERENCES ingestion_runs(run_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    review_key TEXT PRIMARY KEY,
    review_id TEXT,
    run_id TEXT NOT NULL,
    target_id TEXT,
    asin TEXT,
    reviewer_name TEXT,
    rating REAL,
    title TEXT,
    review_date TEXT,
    review_date_iso TEXT,
    variation TEXT,
    verified_purchase INTEGER NOT NULL DEFAULT 0,
    helpful_votes INTEGER NOT NULL DEFAULT 0,
    body TEXT,
    source_url TEXT,
    collected_at TEXT,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES ingestion_runs(run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_review_id
ON reviews(review_id)
WHERE review_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reviews_run_id ON reviews(run_id);
CREATE INDEX IF NOT EXISTS idx_reviews_target_id ON reviews(target_id);
CREATE INDEX IF NOT EXISTS idx_raw_pages_run_id ON raw_pages(run_id);

CREATE TABLE IF NOT EXISTS parse_errors (
    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    target_id TEXT,
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, target_id, error_type),
    FOREIGN KEY (run_id) REFERENCES ingestion_runs(run_id)
);
"""

EXPORT_COLUMNS = (
    "review_key",
    "review_id",
    "run_id",
    "target_id",
    "asin",
    "product_name",
    "category",
    "reviewer_name",
    "rating",
    "title",
    "review_date",
    "review_date_iso",
    "variation",
    "verified_purchase",
    "helpful_votes",
    "body",
    "source_url",
    "collected_at",
    "content_hash",
)


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    migrate_reviewer_schema(connection)
    connection.executescript(SCHEMA)
    ensure_raw_page_storage_columns(connection)
    connection.commit()


def migrate_reviewer_schema(connection: sqlite3.Connection) -> None:
    if not table_exists(connection, "reviews"):
        connection.execute("DROP TABLE IF EXISTS reviewers")
        return
    columns = table_columns(connection, "reviews")
    if "reviewer_hash" not in columns:
        connection.execute("DROP TABLE IF EXISTS reviewers")
        return

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.executescript(
        """
        CREATE TABLE reviews_new (
            review_key TEXT PRIMARY KEY,
            review_id TEXT,
            run_id TEXT NOT NULL,
            target_id TEXT,
            asin TEXT,
            reviewer_name TEXT,
            rating REAL,
            title TEXT,
            review_date TEXT,
            review_date_iso TEXT,
            variation TEXT,
            verified_purchase INTEGER NOT NULL DEFAULT 0,
            helpful_votes INTEGER NOT NULL DEFAULT 0,
            body TEXT,
            source_url TEXT,
            collected_at TEXT,
            content_hash TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES ingestion_runs(run_id)
        );

        INSERT INTO reviews_new (
            review_key, review_id, run_id, target_id, asin, reviewer_name,
            rating, title, review_date, review_date_iso, variation,
            verified_purchase, helpful_votes, body, source_url,
            collected_at, content_hash
        )
        SELECT
            review_key, review_id, run_id, target_id, asin, reviewer_name,
            rating, title, review_date, review_date_iso, variation,
            verified_purchase, helpful_votes, body, source_url,
            collected_at, content_hash
        FROM reviews;

        DROP TABLE reviews;
        ALTER TABLE reviews_new RENAME TO reviews;
        DROP TABLE IF EXISTS reviewers;
        """
    )
    connection.execute("PRAGMA foreign_keys = ON")


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def ensure_raw_page_storage_columns(connection: sqlite3.Connection) -> None:
    if not table_exists(connection, "raw_pages"):
        return
    ensure_column(connection, "raw_pages", "raw_storage", "TEXT NOT NULL DEFAULT 'stored'")
    ensure_column(connection, "raw_pages", "reused_from_run_id", "TEXT")
    ensure_column(connection, "raw_pages", "reused_from_html_path", "TEXT")
    ensure_column(connection, "raw_pages", "blocked_reason", "TEXT")
    ensure_column(connection, "raw_pages", "review_section_detected", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(connection, "raw_pages", "fallback_error_message", "TEXT")
    ensure_column(connection, "raw_pages", "fetch_method", "TEXT")
    ensure_column(connection, "raw_pages", "rendered", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(connection, "raw_pages", "attempt_count", "INTEGER NOT NULL DEFAULT 1")


def ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_definition: str) -> None:
    if column_name not in table_columns(connection, table_name):
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def load_pipeline_run(db_path: Path, parsed_dir: Path, raw_dir: Path, targets_path: Path | None = None) -> dict:
    reviews_path = parsed_dir / "reviews.jsonl"
    parse_report_path = parsed_dir / "parse_report.json"
    metadata_path = raw_dir / "fetch_metadata.jsonl"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Fetch metadata file does not exist: {metadata_path}")

    metadata_by_target = load_fetch_metadata(metadata_path)
    run_id = infer_run_id(raw_dir, metadata_by_target)
    reviews, target_reports, review_source, reviews_path_value = reviews_for_load(reviews_path, raw_dir, metadata_by_target)
    parse_report = read_json_file(parse_report_path) if parse_report_path.exists() else generated_parse_report(raw_dir, reviews, target_reports)
    targets_by_id = load_targets_by_id(targets_path) if targets_path and targets_path.exists() else {}
    loaded_at = utc_now()

    with connect_database(db_path) as connection:
        initialize_database(connection)
        upsert_run(
            connection,
            run_id=run_id,
            raw_dir=raw_dir,
            parsed_dir=parsed_dir,
            targets_path=targets_path,
            loaded_at=loaded_at,
            target_count=int(parse_report.get("target_count") or len(metadata_by_target)),
            raw_page_count=len(metadata_by_target),
            parsed_review_count=len(reviews),
        )

        product_count = upsert_products(connection, metadata_by_target, targets_by_id, run_id)
        raw_page_count = upsert_raw_pages(connection, metadata_by_target, run_id)
        load_summary = insert_reviews(connection, reviews, metadata_by_target, run_id, loaded_at)
        parse_error_count = insert_parse_errors(connection, parse_report, metadata_by_target, run_id, loaded_at)
        connection.commit()

    return {
        "db_path": str(db_path),
        "run_id": run_id,
        "raw_dir": str(raw_dir),
        "parsed_dir": str(parsed_dir),
        "review_source": review_source,
        "reviews_path": reviews_path_value,
        "products_upserted": product_count,
        "raw_pages_upserted": raw_page_count,
        "reviews_seen": len(reviews),
        "reviews_inserted": load_summary["inserted"],
        "duplicates_skipped": load_summary["duplicates"],
        "parse_errors_recorded": parse_error_count,
    }


def reviews_for_load(reviews_path: Path, raw_dir: Path, metadata_by_target: dict[str, dict]) -> tuple[list[dict], list[dict], str, str | None]:
    if reviews_path.exists():
        return read_jsonl(reviews_path), [], "jsonl", str(reviews_path)
    reviews, target_reports = extract_reviews_from_raw_dir(raw_dir, metadata_by_target)
    return reviews, target_reports, "raw_html", None


def generated_parse_report(raw_dir: Path, reviews: list[dict], target_reports: list[dict]) -> dict:
    return {
        "raw_dir": str(raw_dir),
        "keep_jsonl": False,
        "reviews_path": None,
        "target_count": len(target_reports),
        "review_count": len(reviews),
        "targets": target_reports,
    }


def validate_database(db_path: Path, run_id: str | None = None) -> dict:
    with connect_database(db_path) as connection:
        initialize_database(connection)
        error_diagnostics = parse_error_diagnostics(connection, run_id)
        return {
            "db_path": str(db_path),
            "run_id": run_id,
            "counts": {
                "products": scalar(connection, "SELECT COUNT(*) FROM products"),
                "raw_pages": scoped_count(connection, "raw_pages", run_id),
                "reviews": scoped_count(connection, "reviews", run_id),
                "parse_errors": scoped_count(connection, "parse_errors", run_id),
            },
            "quality": quality_metrics(connection, run_id),
            "rating_distribution": rating_distribution(connection, run_id),
            "date_coverage": date_coverage(connection, run_id),
            "targets": target_review_counts(connection, run_id),
            "parse_errors": parse_errors(connection, run_id),
            "parse_error_summary": error_diagnostics["summary"],
            "unresolved_parse_errors": error_diagnostics["unresolved_errors"],
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
        write_export_csv(output_path, rows)
    else:
        write_export_jsonl(output_path, rows)

    return {
        "db_path": str(db_path),
        "output_path": str(output_path),
        "format": output_format,
        "run_id": run_id,
        "review_count": len(rows),
    }


def export_review_rows(connection: sqlite3.Connection, run_id: str | None = None) -> list[dict]:
    where, params = run_scope(run_id, table_alias="r")
    rows = connection.execute(
        f"""
        SELECT
            r.review_key,
            r.review_id,
            r.run_id,
            r.target_id,
            r.asin,
            p.product_name,
            p.category,
            r.reviewer_name,
            r.rating,
            r.title,
            r.review_date,
            r.review_date_iso,
            r.variation,
            r.verified_purchase,
            r.helpful_votes,
            r.body,
            r.source_url,
            r.collected_at,
            r.content_hash
        FROM reviews r
        LEFT JOIN products p
            ON r.target_id = p.target_id
        {where}
        ORDER BY r.target_id, r.review_date_iso, r.review_key
        """,
        params,
    ).fetchall()
    return [normalize_export_row(dict(row)) for row in rows]


def normalize_export_row(row: dict) -> dict:
    row["verified_purchase"] = bool(row["verified_purchase"])
    row["helpful_votes"] = int(row["helpful_votes"] or 0)
    return row


def write_export_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = list(EXPORT_COLUMNS)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_export_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def upsert_run(
    connection: sqlite3.Connection,
    run_id: str,
    raw_dir: Path,
    parsed_dir: Path,
    targets_path: Path | None,
    loaded_at: str,
    target_count: int,
    raw_page_count: int,
    parsed_review_count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO ingestion_runs (
            run_id, raw_dir, parsed_dir, targets_path, loaded_at,
            target_count, raw_page_count, parsed_review_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            raw_dir = excluded.raw_dir,
            parsed_dir = excluded.parsed_dir,
            targets_path = excluded.targets_path,
            loaded_at = excluded.loaded_at,
            target_count = excluded.target_count,
            raw_page_count = excluded.raw_page_count,
            parsed_review_count = excluded.parsed_review_count
        """,
        (
            run_id,
            str(raw_dir),
            str(parsed_dir),
            str(targets_path) if targets_path else None,
            loaded_at,
            target_count,
            raw_page_count,
            parsed_review_count,
        ),
    )


def upsert_products(connection: sqlite3.Connection, metadata_by_target: dict[str, dict], targets_by_id: dict[str, dict], run_id: str) -> int:
    count = 0
    for target_id, metadata in metadata_by_target.items():
        target = targets_by_id.get(target_id, {})
        asin = clean_text(metadata.get("asin")) or clean_text(target.get("asin"))
        product_name = clean_text(target.get("product_name")) or clean_text(metadata.get("product_title"))
        category = clean_text(target.get("category"))
        source_url = clean_text(metadata.get("final_url")) or clean_text(metadata.get("requested_url")) or clean_text(target.get("url"))
        connection.execute(
            """
            INSERT INTO products (
                target_id, asin, product_name, category, source_url,
                first_seen_run_id, last_seen_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                asin = COALESCE(excluded.asin, products.asin),
                product_name = COALESCE(excluded.product_name, products.product_name),
                category = COALESCE(excluded.category, products.category),
                source_url = COALESCE(excluded.source_url, products.source_url),
                last_seen_run_id = excluded.last_seen_run_id
            """,
            (target_id, asin, product_name, category, source_url, run_id, run_id),
        )
        count += 1
    return count


def upsert_raw_pages(connection: sqlite3.Connection, metadata_by_target: dict[str, dict], run_id: str) -> int:
    count = 0
    for target_id, metadata in metadata_by_target.items():
        connection.execute(
            """
            INSERT INTO raw_pages (
                page_key, run_id, target_id, asin, requested_url, final_url,
                status, status_code, fetched_at, html_path, raw_storage,
                reused_from_run_id, reused_from_html_path, content_hash,
                blocked_or_signin, blocked_reason, review_section_detected,
                response_bytes, page_title, product_title, error_message,
                fallback_error_message, fetch_method, rendered, attempt_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, target_id) DO UPDATE SET
                asin = excluded.asin,
                requested_url = excluded.requested_url,
                final_url = excluded.final_url,
                status = excluded.status,
                status_code = excluded.status_code,
                fetched_at = excluded.fetched_at,
                html_path = excluded.html_path,
                raw_storage = excluded.raw_storage,
                reused_from_run_id = excluded.reused_from_run_id,
                reused_from_html_path = excluded.reused_from_html_path,
                content_hash = excluded.content_hash,
                blocked_or_signin = excluded.blocked_or_signin,
                blocked_reason = excluded.blocked_reason,
                review_section_detected = excluded.review_section_detected,
                response_bytes = excluded.response_bytes,
                page_title = excluded.page_title,
                product_title = excluded.product_title,
                error_message = excluded.error_message,
                fallback_error_message = excluded.fallback_error_message,
                fetch_method = excluded.fetch_method,
                rendered = excluded.rendered,
                attempt_count = excluded.attempt_count
            """,
            (
                f"{run_id}:{target_id}",
                run_id,
                target_id,
                clean_text(metadata.get("asin")),
                clean_text(metadata.get("requested_url")),
                clean_text(metadata.get("final_url")),
                clean_text(metadata.get("status")),
                metadata.get("status_code"),
                clean_text(metadata.get("fetched_at")),
                clean_text(metadata.get("html_path")),
                clean_text(metadata.get("raw_storage")) or "stored",
                clean_text(metadata.get("reused_from_run_id")),
                clean_text(metadata.get("reused_from_html_path")),
                clean_text(metadata.get("content_hash")),
                int(bool(metadata.get("blocked_or_signin"))),
                clean_text(metadata.get("blocked_reason")),
                int(bool(metadata.get("review_section_detected"))),
                int(metadata.get("response_bytes") or 0),
                clean_text(metadata.get("page_title")),
                clean_text(metadata.get("product_title")),
                clean_text(metadata.get("error_message")),
                clean_text(metadata.get("fallback_error_message")),
                clean_text(metadata.get("fetch_method")),
                int(bool(metadata.get("rendered"))),
                int(metadata.get("attempt_count") or 1),
            ),
        )
        count += 1
    return count


def insert_reviews(
    connection: sqlite3.Connection,
    reviews: list[dict],
    metadata_by_target: dict[str, dict],
    run_id: str,
    loaded_at: str,
) -> dict:
    inserted = 0
    duplicates = 0
    for review in reviews:
        target_id = clean_text(review.get("target_id"))
        metadata = metadata_by_target.get(target_id or "", {})
        reviewer_name = clean_text(review.get("reviewer_name"))

        review_key = stable_review_key(review)
        content_hash = stable_review_content_hash(review)
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO reviews (
                review_key, review_id, run_id, target_id, asin, reviewer_name,
                rating, title, review_date, review_date_iso,
                variation, verified_purchase, helpful_votes, body, source_url,
                collected_at, content_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_key,
                clean_text(review.get("review_id")),
                run_id,
                target_id,
                clean_text(review.get("asin")),
                reviewer_name,
                review.get("rating"),
                clean_text(review.get("title")),
                clean_text(review.get("review_date")),
                extract_review_iso_date(clean_text(review.get("review_date"))),
                clean_text(review.get("variation")),
                int(bool(review.get("verified_purchase"))),
                int(review.get("helpful_votes") or 0),
                clean_text(review.get("body")),
                clean_text(review.get("source_url")),
                clean_text(metadata.get("fetched_at")) or loaded_at,
                content_hash,
            ),
        )
        if cursor.rowcount:
            inserted += 1
        else:
            duplicates += 1
    return {"inserted": inserted, "duplicates": duplicates}


def insert_parse_errors(
    connection: sqlite3.Connection,
    parse_report: dict,
    metadata_by_target: dict[str, dict],
    run_id: str,
    created_at: str,
) -> int:
    count = 0
    for target in parse_report.get("targets", []):
        target_id = clean_text(target.get("target_id"))
        if not target_id:
            continue
        metadata = metadata_by_target.get(target_id, {})
        error_type = parse_error_type(target, metadata)
        if not error_type:
            continue
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO parse_errors (run_id, target_id, error_type, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, target_id, error_type, parse_error_message(error_type, target_id), created_at),
        )
        count += cursor.rowcount
    return count


def parse_error_type(target_report: dict, metadata: dict) -> str | None:
    if metadata.get("status") == "fetch_error":
        return "fetch_error"
    if metadata.get("blocked_or_signin"):
        return "blocked_or_signin"
    if int(target_report.get("review_count") or 0) == 0:
        return "no_reviews_found"
    if int(target_report.get("non_empty_bodies") or 0) < int(target_report.get("review_count") or 0):
        return "missing_review_body"
    return None


def parse_error_message(error_type: str, target_id: str) -> str:
    messages = {
        "fetch_error": "Fetch failed for target.",
        "blocked_or_signin": "Amazon returned a blocked or sign-in page.",
        "no_reviews_found": "No top-review containers were parsed for target.",
        "missing_review_body": "At least one parsed review had no body text.",
    }
    return f"{messages.get(error_type, 'Parse issue detected')} Target: {target_id}"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_json_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_targets_by_id(path: Path) -> dict[str, dict]:
    return {target.target_id: target.__dict__ for target in load_targets(path)}


def stable_review_key(review: dict) -> str:
    review_id = clean_text(review.get("review_id"))
    if review_id:
        return f"review:{review_id}"
    return f"hash:{stable_review_content_hash(review)}"


def stable_review_content_hash(review: dict) -> str:
    fields = [
        clean_text(review.get("target_id")),
        clean_text(review.get("asin")),
        clean_text(review.get("reviewer_name")),
        str(review.get("rating") if review.get("rating") is not None else ""),
        clean_text(review.get("title")),
        clean_text(review.get("review_date")),
        clean_text(review.get("body")),
    ]
    return sha256_text("|".join(field or "" for field in fields))


def extract_review_iso_date(review_date: str | None) -> str | None:
    if not review_date:
        return None
    match = re.search(r"\bon\s+([A-Za-z]+ \d{1,2}, \d{4})", review_date)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scalar(connection: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    return int(connection.execute(query, params).fetchone()[0])


def scoped_count(connection: sqlite3.Connection, table: str, run_id: str | None) -> int:
    if run_id:
        return scalar(connection, f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,))
    return scalar(connection, f"SELECT COUNT(*) FROM {table}")


def quality_metrics(connection: sqlite3.Connection, run_id: str | None) -> dict:
    where, params = run_scope(run_id)
    return {
        "missing_review_id": scalar(connection, f"SELECT COUNT(*) FROM reviews {where} AND review_id IS NULL", params),
        "missing_body": scalar(connection, f"SELECT COUNT(*) FROM reviews {where} AND body IS NULL", params),
        "missing_rating": scalar(connection, f"SELECT COUNT(*) FROM reviews {where} AND rating IS NULL", params),
        "missing_review_date": scalar(connection, f"SELECT COUNT(*) FROM reviews {where} AND review_date IS NULL", params),
        "duplicate_review_ids": scalar(
            connection,
            f"""
            SELECT COUNT(*) FROM (
                SELECT review_id FROM reviews {where}
                AND review_id IS NOT NULL
                GROUP BY review_id
                HAVING COUNT(*) > 1
            )
            """,
            params,
        ),
    }


def rating_distribution(connection: sqlite3.Connection, run_id: str | None) -> dict[str, int]:
    where, params = run_scope(run_id)
    rows = connection.execute(
        f"""
        SELECT rating, COUNT(*) AS count
        FROM reviews
        {where}
        AND rating IS NOT NULL
        GROUP BY rating
        ORDER BY rating
        """,
        params,
    ).fetchall()
    return {str(row["rating"]): int(row["count"]) for row in rows}


def date_coverage(connection: sqlite3.Connection, run_id: str | None) -> dict:
    where, params = run_scope(run_id)
    row = connection.execute(
        f"""
        SELECT
            COUNT(*) AS reviews,
            SUM(CASE WHEN review_date IS NOT NULL THEN 1 ELSE 0 END) AS review_date_text_count,
            SUM(CASE WHEN review_date_iso IS NOT NULL THEN 1 ELSE 0 END) AS parsed_date_count,
            MIN(review_date_iso) AS earliest_review_date,
            MAX(review_date_iso) AS latest_review_date
        FROM reviews
        {where}
        """,
        params,
    ).fetchone()
    return {
        "reviews": int(row["reviews"] or 0),
        "review_date_text_count": int(row["review_date_text_count"] or 0),
        "parsed_date_count": int(row["parsed_date_count"] or 0),
        "earliest_review_date": row["earliest_review_date"],
        "latest_review_date": row["latest_review_date"],
    }


def target_review_counts(connection: sqlite3.Connection, run_id: str | None) -> list[dict]:
    where, params = run_scope(run_id, table_alias="p")
    rows = connection.execute(
        f"""
        SELECT
            p.target_id,
            p.status,
            p.raw_storage,
            p.blocked_or_signin,
            COUNT(r.review_key) AS review_count,
            SUM(CASE WHEN r.body IS NOT NULL THEN 1 ELSE 0 END) AS non_empty_bodies
        FROM raw_pages p
        LEFT JOIN reviews r
            ON p.run_id = r.run_id
            AND p.target_id = r.target_id
        {where}
        GROUP BY p.target_id, p.status, p.raw_storage, p.blocked_or_signin
        ORDER BY p.target_id
        """,
        params,
    ).fetchall()
    return [
        {
            "target_id": row["target_id"],
            "status": row["status"],
            "raw_storage": row["raw_storage"],
            "blocked_or_signin": bool(row["blocked_or_signin"]),
            "review_count": int(row["review_count"] or 0),
            "non_empty_bodies": int(row["non_empty_bodies"] or 0),
        }
        for row in rows
    ]


def parse_errors(connection: sqlite3.Connection, run_id: str | None) -> list[dict]:
    where, params = run_scope(run_id)
    rows = connection.execute(
        f"""
        SELECT run_id, target_id, error_type, message, created_at
        FROM parse_errors
        {where}
        ORDER BY target_id, error_type
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def parse_error_diagnostics(connection: sqlite3.Connection, run_id: str | None) -> dict:
    errors = parse_errors(connection, run_id)
    target_ids = sorted({error["target_id"] for error in errors if error.get("target_id")})
    target_statuses = current_target_statuses(connection, target_ids)
    detailed_errors = []

    for error in errors:
        status = target_statuses.get(error.get("target_id") or "", {})
        resolution_status, resolution_reason = classify_parse_error(error, status)
        detailed_error = {
            **error,
            "resolution_status": resolution_status,
            "resolution_reason": resolution_reason,
            "current_review_count": int(status.get("review_count") or 0),
            "current_non_empty_body_count": int(status.get("non_empty_body_count") or 0),
            "latest_raw_page": status.get("latest_raw_page"),
        }
        detailed_errors.append(detailed_error)

    unresolved_errors = [
        error for error in detailed_errors if error["resolution_status"] == "currently_unresolved"
    ]
    resolved_errors = [
        error for error in detailed_errors if error["resolution_status"] == "resolved"
    ]
    unresolved_target_ids = sorted({error["target_id"] for error in unresolved_errors if error.get("target_id")})
    resolved_target_ids = sorted({error["target_id"] for error in resolved_errors if error.get("target_id")})

    return {
        "summary": {
            "classification_scope": "current_database_state",
            "total_errors": len(errors),
            "resolved_errors": len(resolved_errors),
            "currently_unresolved_errors": len(unresolved_errors),
            "unique_error_targets": len(target_ids),
            "unique_resolved_targets": len(resolved_target_ids),
            "unique_currently_unresolved_targets": len(unresolved_target_ids),
            "currently_unresolved_targets": unresolved_target_ids,
            "by_type": dict(Counter(error["error_type"] for error in errors)),
            "resolved_by_type": dict(Counter(error["error_type"] for error in resolved_errors)),
            "currently_unresolved_by_type": dict(
                Counter(error["error_type"] for error in unresolved_errors)
            ),
            "resolution_reasons": dict(
                Counter(error["resolution_reason"] for error in detailed_errors)
            ),
        },
        "unresolved_errors": unresolved_errors,
    }


def current_target_statuses(connection: sqlite3.Connection, target_ids: list[str]) -> dict[str, dict]:
    if not target_ids:
        return {}

    placeholders = ",".join("?" for _ in target_ids)
    statuses = {
        target_id: {
            "review_count": 0,
            "non_empty_body_count": 0,
            "latest_raw_page": None,
        }
        for target_id in target_ids
    }

    review_rows = connection.execute(
        f"""
        SELECT
            target_id,
            COUNT(*) AS review_count,
            SUM(CASE WHEN body IS NOT NULL THEN 1 ELSE 0 END) AS non_empty_body_count
        FROM reviews
        WHERE target_id IN ({placeholders})
        GROUP BY target_id
        """,
        tuple(target_ids),
    ).fetchall()
    for row in review_rows:
        statuses[row["target_id"]]["review_count"] = int(row["review_count"] or 0)
        statuses[row["target_id"]]["non_empty_body_count"] = int(row["non_empty_body_count"] or 0)

    raw_rows = connection.execute(
        f"""
        SELECT
            target_id,
            run_id,
            status,
            blocked_or_signin,
            blocked_reason,
            review_section_detected,
            fetch_method,
            rendered,
            fetched_at,
            error_message
        FROM raw_pages
        WHERE target_id IN ({placeholders})
        ORDER BY target_id, COALESCE(fetched_at, '') DESC, run_id DESC
        """,
        tuple(target_ids),
    ).fetchall()
    for row in raw_rows:
        target_status = statuses[row["target_id"]]
        if target_status["latest_raw_page"] is not None:
            continue
        target_status["latest_raw_page"] = {
            "run_id": row["run_id"],
            "status": row["status"],
            "blocked_or_signin": bool(row["blocked_or_signin"]),
            "blocked_reason": row["blocked_reason"],
            "review_section_detected": bool(row["review_section_detected"]),
            "fetch_method": row["fetch_method"],
            "rendered": bool(row["rendered"]),
            "fetched_at": row["fetched_at"],
            "error_message": row["error_message"],
        }

    return statuses


def classify_parse_error(error: dict, target_status: dict) -> tuple[str, str]:
    error_type = error.get("error_type")
    review_count = int(target_status.get("review_count") or 0)
    non_empty_body_count = int(target_status.get("non_empty_body_count") or 0)
    latest = target_status.get("latest_raw_page")

    if error_type == "missing_review_body":
        if review_count > 0 and non_empty_body_count == review_count:
            return "resolved", "target_reviews_now_have_bodies"
        return "currently_unresolved", unresolved_reason_from_latest(latest)

    if error_type == "no_reviews_found":
        if review_count > 0:
            return "resolved", "target_has_loaded_reviews"
        if latest and latest.get("status") == "fetched" and latest.get("review_section_detected"):
            return "resolved", "latest_fetch_detected_review_section"
        return "currently_unresolved", unresolved_reason_from_latest(latest)

    if error_type == "blocked_or_signin":
        if latest and latest.get("status") == "fetched" and not latest.get("blocked_or_signin"):
            return "resolved", "latest_fetch_not_blocked"
        if review_count > 0:
            return "resolved", "target_has_loaded_reviews"
        return "currently_unresolved", unresolved_reason_from_latest(latest)

    if error_type == "fetch_error":
        if latest and latest.get("status") == "fetched" and not latest.get("blocked_or_signin"):
            return "resolved", "latest_fetch_succeeded"
        if review_count > 0:
            return "resolved", "target_has_loaded_reviews"
        return "currently_unresolved", unresolved_reason_from_latest(latest)

    if review_count > 0:
        return "resolved", "target_has_loaded_reviews"
    return "currently_unresolved", unresolved_reason_from_latest(latest)


def unresolved_reason_from_latest(latest_raw_page: dict | None) -> str:
    if not latest_raw_page:
        return "no_raw_page_recorded"
    if latest_raw_page.get("blocked_or_signin") or latest_raw_page.get("status") == "blocked":
        return "latest_fetch_blocked_or_signin"
    if latest_raw_page.get("status") == "fetch_error":
        return "latest_fetch_error"
    if latest_raw_page.get("status") == "fetched" and not latest_raw_page.get("review_section_detected"):
        return "latest_fetch_has_no_review_section"
    return "latest_fetch_still_unresolved"


def run_scope(run_id: str | None, table_alias: str | None = None) -> tuple[str, tuple]:
    column = f"{table_alias}.run_id" if table_alias else "run_id"
    if run_id:
        return f"WHERE {column} = ?", (run_id,)
    return "WHERE 1 = 1", ()
