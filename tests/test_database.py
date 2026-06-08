import json
import sqlite3

from amazon_review_pipeline.database import initialize_database, load_pipeline_run, stable_review_key, validate_database


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_load_pipeline_run_creates_schema_and_is_idempotent(tmp_path):
    db_path = tmp_path / "reviews.sqlite"
    raw_dir = tmp_path / "raw" / "run-test"
    parsed_dir = tmp_path / "parsed" / "run-test"
    raw_dir.mkdir(parents=True)
    parsed_dir.mkdir(parents=True)

    metadata_rows = [
        {
            "run_id": "run-test",
            "target_id": "amzn_b0dz75tn5f",
            "asin": "B0DZ75TN5F",
            "requested_url": "https://www.amazon.com/dp/B0DZ75TN5F/",
            "final_url": "https://www.amazon.com/dp/B0DZ75TN5F/",
            "status": "fetched",
            "status_code": 200,
            "fetched_at": "2026-06-08T19:39:01+00:00",
            "html_path": "data/raw/run-test/amzn_b0dz75tn5f.html",
            "content_hash": "abc",
            "blocked_or_signin": False,
            "response_bytes": 123,
            "page_title": "Amazon product",
            "product_title": "Apple iPad",
            "error_message": None,
        },
        {
            "run_id": "run-test",
            "target_id": "amzn_empty",
            "asin": "B000000000",
            "requested_url": "https://www.amazon.com/dp/B000000000/",
            "final_url": "https://www.amazon.com/dp/B000000000/",
            "status": "fetched",
            "status_code": 200,
            "fetched_at": "2026-06-08T19:39:02+00:00",
            "html_path": "data/raw/run-test/amzn_empty.html",
            "content_hash": "def",
            "blocked_or_signin": False,
            "response_bytes": 456,
            "page_title": "Amazon product",
            "product_title": "No Review Product",
            "error_message": None,
        },
    ]
    write_jsonl(raw_dir / "fetch_metadata.jsonl", metadata_rows)

    reviews = [
        {
            "target_id": "amzn_b0dz75tn5f",
            "review_id": "R1",
            "asin": "B0DZ75TN5F",
            "reviewer_name": "Alice",
            "rating": 5.0,
            "title": "Great",
            "review_date": "Reviewed in Brazil on November 14, 2025",
            "variation": "Color: Blue",
            "verified_purchase": True,
            "helpful_votes": 3,
            "body": "Works well.",
            "source_url": "https://www.amazon.com/dp/B0DZ75TN5F/",
        },
        {
            "target_id": "amzn_b0dz75tn5f",
            "review_id": "",
            "asin": "B0DZ75TN5F",
            "reviewer_name": "Bob",
            "rating": 4.0,
            "title": "Good",
            "review_date": "Reviewed in the United States on June 1, 2026",
            "variation": None,
            "verified_purchase": False,
            "helpful_votes": 0,
            "body": "Good value.",
            "source_url": "https://www.amazon.com/dp/B0DZ75TN5F/",
        },
    ]
    write_jsonl(parsed_dir / "reviews.jsonl", reviews)
    (parsed_dir / "parse_report.json").write_text(
        json.dumps(
            {
                "target_count": 2,
                "review_count": 2,
                "targets": [
                    {"target_id": "amzn_b0dz75tn5f", "review_count": 2, "non_empty_bodies": 2},
                    {"target_id": "amzn_empty", "review_count": 0, "non_empty_bodies": 0},
                ],
            }
        ),
        encoding="utf-8",
    )

    first_summary = load_pipeline_run(db_path, parsed_dir, raw_dir)
    second_summary = load_pipeline_run(db_path, parsed_dir, raw_dir)
    report = validate_database(db_path, "run-test")

    assert first_summary["reviews_inserted"] == 2
    assert first_summary["duplicates_skipped"] == 0
    assert first_summary["parse_errors_recorded"] == 1
    assert second_summary["reviews_inserted"] == 0
    assert second_summary["duplicates_skipped"] == 2
    assert report["counts"]["products"] == 2
    assert report["counts"]["raw_pages"] == 2
    assert report["counts"]["reviews"] == 2
    assert report["counts"]["parse_errors"] == 1
    assert report["quality"]["missing_review_id"] == 1
    assert report["rating_distribution"] == {"4.0": 1, "5.0": 1}
    assert report["date_coverage"]["earliest_review_date"] == "2025-11-14"
    assert report["date_coverage"]["latest_review_date"] == "2026-06-01"

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"products", "reviews", "raw_pages", "ingestion_runs", "parse_errors"}.issubset(tables)
    assert "reviewers" not in tables

    with sqlite3.connect(db_path) as connection:
        review_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(reviews)")
        }
    assert "reviewer_name" in review_columns
    assert "reviewer_hash" not in review_columns


def test_load_pipeline_run_can_parse_raw_html_without_jsonl(tmp_path):
    db_path = tmp_path / "reviews.sqlite"
    raw_dir = tmp_path / "raw" / "run-raw"
    parsed_dir = tmp_path / "parsed" / "run-raw"
    raw_dir.mkdir(parents=True)
    parsed_dir.mkdir(parents=True)
    write_jsonl(
        raw_dir / "fetch_metadata.jsonl",
        [
            {
                "run_id": "run-raw",
                "target_id": "target",
                "asin": "B0TEST0001",
                "requested_url": "https://www.amazon.com/dp/B0TEST0001/",
                "final_url": "https://www.amazon.com/dp/B0TEST0001/",
                "status": "fetched",
                "status_code": 200,
                "fetched_at": "2026-06-08T19:39:01+00:00",
                "html_path": "target.html",
                "content_hash": "abc",
                "blocked_or_signin": False,
                "response_bytes": 123,
                "page_title": "Amazon product",
                "product_title": "Test Product",
                "error_message": None,
            }
        ],
    )
    (raw_dir / "target.html").write_text(
        """
        <div id="localTopReviewsList">
          <div data-hook="reviewContainer" data-reviewid="R2" data-asin="B0TEST0001">
            <span class="a-profile-name">Alice</span>
            <i data-hook="review-star-rating"><span class="a-icon-alt">5.0 out of 5 stars</span></i>
            <a data-hook="reviewTitle">Excellent</a>
            <span data-hook="review-date">Reviewed in Canada on May 2, 2026</span>
            <span data-hook="avp-badge">Verified Purchase</span>
            <span data-hook="helpful-vote-statement">One person found this helpful</span>
            <span data-hook="reviewText">Compact and reliable.</span>
          </div>
        </div>
        """,
        encoding="utf-8",
    )
    (parsed_dir / "parse_report.json").write_text(
        json.dumps(
            {
                "raw_dir": str(raw_dir),
                "keep_jsonl": False,
                "reviews_path": None,
                "target_count": 1,
                "review_count": 1,
                "targets": [{"target_id": "target", "review_count": 1, "non_empty_bodies": 1}],
            }
        ),
        encoding="utf-8",
    )

    summary = load_pipeline_run(db_path, parsed_dir, raw_dir)
    report = validate_database(db_path, "run-raw")

    assert summary["review_source"] == "raw_html"
    assert summary["reviews_path"] is None
    assert summary["reviews_inserted"] == 1
    assert report["counts"]["reviews"] == 1
    assert report["rating_distribution"] == {"5.0": 1}


def test_stable_review_key_prefers_review_id_and_hashes_fallback():
    with_id = {"review_id": "R1", "body": "A"}
    without_id = {
        "target_id": "amzn_b0dz75tn5f",
        "asin": "B0DZ75TN5F",
        "reviewer_name": "Alice",
        "rating": 5.0,
        "title": "Great",
        "review_date": "Reviewed in Brazil on November 14, 2025",
        "body": "Works well.",
    }

    assert stable_review_key(with_id) == "review:R1"
    assert stable_review_key(without_id).startswith("hash:")
    assert stable_review_key(without_id) == stable_review_key(dict(without_id))


def test_initialize_database_migrates_obsolete_reviewer_table(tmp_path):
    db_path = tmp_path / "old.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE ingestion_runs (
                run_id TEXT PRIMARY KEY,
                raw_dir TEXT,
                parsed_dir TEXT,
                targets_path TEXT,
                loaded_at TEXT NOT NULL,
                target_count INTEGER NOT NULL DEFAULT 0,
                raw_page_count INTEGER NOT NULL DEFAULT 0,
                parsed_review_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE reviewers (
                reviewer_hash TEXT PRIMARY KEY,
                reviewer_name TEXT,
                first_seen_run_id TEXT
            );
            CREATE TABLE reviews (
                review_key TEXT PRIMARY KEY,
                review_id TEXT,
                run_id TEXT NOT NULL,
                target_id TEXT,
                asin TEXT,
                reviewer_hash TEXT,
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
                FOREIGN KEY (reviewer_hash) REFERENCES reviewers(reviewer_hash)
            );
            INSERT INTO ingestion_runs (run_id, loaded_at) VALUES ('run-test', '2026-06-08T00:00:00+00:00');
            INSERT INTO reviewers (reviewer_hash, reviewer_name, first_seen_run_id) VALUES ('hash-a', 'Alice', 'run-test');
            INSERT INTO reviews (
                review_key, review_id, run_id, target_id, asin, reviewer_hash,
                reviewer_name, rating, title, review_date, review_date_iso,
                variation, verified_purchase, helpful_votes, body, source_url,
                collected_at, content_hash
            )
            VALUES (
                'review:R1', 'R1', 'run-test', 'target', 'ASIN', 'hash-a',
                'Alice', 5.0, 'Great', 'Reviewed in Brazil on November 14, 2025',
                '2025-11-14', NULL, 1, 0, 'Works well.', 'https://example.test',
                '2026-06-08T00:00:00+00:00', 'content-hash'
            );
            """
        )
        initialize_database(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        review_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(reviews)")
        }
        review_count = connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]

    assert "reviewers" not in tables
    assert "reviewer_hash" not in review_columns
    assert "reviewer_name" in review_columns
    assert review_count == 1
