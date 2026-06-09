import argparse
import json
import sqlite3
from pathlib import Path

from amazon_review_pipeline.daily import (
    apply_fetch_metadata_to_state,
    chunk_targets,
    run_daily_pipeline,
    select_due_targets,
    safety_stop_reason,
    sync_state_with_targets,
)
from amazon_review_pipeline.models import Target
from amazon_review_pipeline.utils import sha256_text


NOW = "2026-06-09T12:00:00+00:00"


def target(target_id: str, active: bool = True) -> Target:
    asin = target_id.removeprefix("amzn_").upper().ljust(10, "0")[:10]
    return Target(
        target_id=target_id,
        url=f"https://www.amazon.com/dp/{asin}/",
        asin=asin,
        product_name=target_id,
        category="best_sellers",
        active=active,
        notes=None,
    )


def test_select_due_targets_prioritizes_new_stale_and_retryable_targets():
    targets = [
        target("amzn_new"),
        target("amzn_recent"),
        target("amzn_stale"),
        target("amzn_blocked"),
        target("amzn_error"),
        target("amzn_inactive", active=False),
    ]
    state = {
        "targets": {
            "amzn_recent": {
                "last_fetch_attempt_at": "2026-06-08T12:00:00+00:00",
                "last_successful_fetch_at": "2026-06-08T12:00:00+00:00",
                "last_status": "fetched",
            },
            "amzn_stale": {
                "last_fetch_attempt_at": "2026-05-20T12:00:00+00:00",
                "last_successful_fetch_at": "2026-05-20T12:00:00+00:00",
                "last_status": "fetched",
            },
            "amzn_blocked": {
                "last_fetch_attempt_at": "2026-06-08T12:00:00+00:00",
                "last_status": "blocked",
            },
            "amzn_error": {
                "last_fetch_attempt_at": "2026-06-07T12:00:00+00:00",
                "last_status": "fetch_error",
            },
        }
    }

    due = select_due_targets(
        targets,
        state,
        now=NOW,
        stale_days=7,
        blocked_cooldown_days=3,
        error_retry_days=1,
    )

    assert [item.target_id for item in due] == ["amzn_new", "amzn_error", "amzn_stale"]


def test_chunk_targets_splits_backlog_into_batches():
    targets = [target(f"amzn_{index:010d}") for index in range(120)]

    batches = chunk_targets(targets, 50)

    assert [len(batch) for batch in batches] == [50, 50, 20]


def test_sync_and_fetch_metadata_update_state():
    state = {"version": 1, "targets": {}}
    targets = [target("amzn_state")]

    sync_state_with_targets(state, targets, {"amzn_state"}, NOW)
    apply_fetch_metadata_to_state(
        state,
        [
            {
                "target_id": "amzn_state",
                "status": "fetched",
                "status_code": 200,
                "fetched_at": NOW,
                "content_hash": "hash-1",
            }
        ],
    )
    apply_fetch_metadata_to_state(
        state,
        [
            {
                "target_id": "amzn_blocked",
                "status": "blocked",
                "status_code": 503,
                "fetched_at": NOW,
                "content_hash": "hash-2",
            },
            {
                "target_id": "amzn_error",
                "status": "fetch_error",
                "status_code": None,
                "fetched_at": NOW,
                "content_hash": None,
            },
        ],
    )

    assert state["targets"]["amzn_state"]["first_seen_at"] == NOW
    assert state["targets"]["amzn_state"]["last_discovered_at"] == NOW
    assert state["targets"]["amzn_state"]["last_successful_fetch_at"] == NOW
    assert state["targets"]["amzn_state"]["latest_content_hash"] == "hash-1"
    assert state["targets"]["amzn_blocked"]["block_count"] == 1
    assert state["targets"]["amzn_error"]["fetch_error_count"] == 1


def test_safety_stop_reason_detects_block_limits():
    reports = [
        {
            "metadata_rows": [
                {"status": "fetched"},
                {"status": "blocked"},
                {"status": "blocked"},
                {"status": "blocked"},
            ]
        }
    ]

    assert (
        safety_stop_reason(
            reports,
            start_monotonic=10**12,
            max_runtime_minutes=300,
            max_block_rate=0.25,
            max_consecutive_blocked=5,
        )
        == "max_block_rate_reached"
    )
    assert (
        safety_stop_reason(
            reports,
            start_monotonic=10**12,
            max_runtime_minutes=300,
            max_block_rate=1.0,
            max_consecutive_blocked=3,
        )
        == "max_consecutive_blocked_reached"
    )


def test_daily_pipeline_drains_batches_with_cooldown_and_loads_reviews(tmp_path, monkeypatch):
    targets_path = tmp_path / "amazon_products.csv"
    state_path = tmp_path / "state" / "pipeline_state.json"
    targets_path.parent.mkdir(parents=True, exist_ok=True)
    targets_path.write_text("target_id,url,asin,product_name,category,active,notes\n", encoding="utf-8")

    def fake_discovery(discovery_args):
        rows = [
            "target_id,url,asin,product_name,category,active,notes",
            "amzn_b000000001,https://www.amazon.com/dp/B000000001/,B000000001,Product One,best_sellers,true,discovered",
            "amzn_b000000002,https://www.amazon.com/dp/B000000002/,B000000002,Product Two,best_sellers,true,discovered",
        ]
        targets_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        discovery_dir = discovery_args.discovery_root / "discovery-test"
        discovery_dir.mkdir(parents=True)
        products_path = discovery_dir / "bestseller_products.jsonl"
        products = [
            {"target_id": "amzn_b000000001", "asin": "B000000001"},
            {"target_id": "amzn_b000000002", "asin": "B000000002"},
        ]
        products_path.write_text("".join(json.dumps(row) + "\n" for row in products), encoding="utf-8")
        return {"run_id": "discovery-test", "products_path": str(products_path), "discovered_products": 2}

    def fake_fetch(target, output_dir, timeout, content_hash_index, fetch_method="requests"):
        html = f"""
        <div id="localTopReviewsList">
          <div data-hook="reviewContainer" data-reviewid="R-{target.asin}" data-asin="{target.asin}">
            <span class="a-profile-name">Alice</span>
            <i data-hook="review-star-rating"><span class="a-icon-alt">5.0 out of 5 stars</span></i>
            <a data-hook="reviewTitle">Excellent</a>
            <span data-hook="review-date">Reviewed in Canada on May 2, 2026</span>
            <span data-hook="avp-badge">Verified Purchase</span>
            <span data-hook="helpful-vote-statement">One person found this helpful</span>
            <span data-hook="reviewText">Ready for daily loading.</span>
          </div>
        </div>
        """
        html_path = output_dir / f"{target.target_id}.html"
        html_path.write_text(html, encoding="utf-8")
        return {
            "target_id": target.target_id,
            "asin": target.asin,
            "requested_url": target.url,
            "final_url": target.url,
            "status": "fetched",
            "status_code": 200,
            "fetched_at": NOW,
            "html_path": str(html_path),
            "content_hash": sha256_text(html),
            "blocked_or_signin": False,
            "blocked_reason": None,
            "review_section_detected": True,
            "response_bytes": len(html.encode("utf-8")),
            "page_title": "Amazon product",
            "product_title": target.product_name,
            "error_message": None,
            "fallback_error_message": None,
            "raw_storage": "stored",
            "reused_from_run_id": None,
            "reused_from_html_path": None,
            "fetch_method": fetch_method,
            "rendered": False,
            "attempt_count": 1,
        }

    sleep_calls = []
    monkeypatch.setattr("amazon_review_pipeline.daily.run_discovery", fake_discovery)
    monkeypatch.setattr("amazon_review_pipeline.daily.fetch_target", fake_fetch)

    report = run_daily_pipeline(
        argparse.Namespace(
            targets=targets_path,
            state=state_path,
            raw_root=tmp_path / "raw",
            parsed_root=tmp_path / "parsed",
            reports_root=tmp_path / "reports",
            discovery_root=tmp_path / "discovery",
            db=tmp_path / "reviews.sqlite",
            export_csv=tmp_path / "exports" / "reviews.csv",
            seed_url="https://www.amazon.com/gp/bestsellers/",
            max_seed_pages=1,
            max_products_per_page=0,
            discovery_delay=0,
            timeout=1,
            fetch_method="auto",
            batch_size=1,
            batch_cooldown_minutes=10,
            target_delay_seconds=0,
            stale_days=7,
            blocked_cooldown_days=3,
            error_retry_days=1,
            max_runtime_minutes=300,
            max_block_rate=1.0,
            max_consecutive_blocked=5,
        ),
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
    )

    assert report["queue"]["due_targets"] == 2
    assert report["queue"]["batches_completed"] == 2
    assert report["batch_reports"][0]["fetch_summary"]["fetch_methods"] == {"auto": 1}
    assert sleep_calls == [600]
    assert Path(report["report_path"]).exists()
    assert state_path.exists()
    assert (tmp_path / "exports" / "reviews.csv").exists()
    with sqlite3.connect(tmp_path / "reviews.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2
