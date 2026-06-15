import csv
import json
import sqlite3
from pathlib import Path

import pytest

from steam_review_pipeline.database import export_reviews, load_pipeline_run, validate_database
from steam_review_pipeline.fetcher import build_review_url, fetch_app_reviews, sanitize_payload_for_storage
from steam_review_pipeline.files import write_json, write_jsonl
from steam_review_pipeline.models import SteamApp
from steam_review_pipeline.targets import load_targets


def steam_payload(cursor="next-cursor", reviews=None, total_reviews=2):
    return {
        "success": 1,
        "query_summary": {
            "num_reviews": len(reviews or []),
            "review_score": 8,
            "review_score_desc": "Very Positive",
            "total_positive": 10,
            "total_negative": 1,
            "total_reviews": total_reviews,
        },
        "cursor": cursor,
        "reviews": reviews or [],
    }


def steam_review(recommendationid="1001", text="A very complete review.", updated=1):
    return {
        "recommendationid": recommendationid,
        "author": {
            "steamid": "76561198000000000",
            "playtime_forever": 120,
            "playtime_last_two_weeks": 5,
            "playtime_at_review": 90,
            "last_played": 1700000000,
        },
        "language": "english",
        "review": text,
        "timestamp_created": 1600000000,
        "timestamp_updated": updated,
        "voted_up": True,
        "votes_up": 7,
        "votes_funny": 2,
        "weighted_vote_score": "0.75",
        "comment_count": 3,
        "steam_purchase": True,
        "received_for_free": False,
        "written_during_early_access": False,
        "primarily_steam_deck": True,
    }


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else steam_payload()

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if not self.responses:
            raise AssertionError("No fake responses left")
        return self.responses.pop(0)


def write_targets(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["app_id", "app_name", "active", "notes"])
        writer.writeheader()
        writer.writerow({"app_id": "730", "app_name": "Counter-Strike 2", "active": "true", "notes": "seed"})
        writer.writerow({"app_id": "999", "app_name": "Inactive", "active": "false", "notes": ""})


def test_load_steam_targets_and_encode_cursor(tmp_path):
    targets_path = tmp_path / "steam_apps.csv"
    write_targets(targets_path)

    targets = load_targets(targets_path)
    url = build_review_url("730", cursor="AoIIPw==")

    assert len(targets) == 2
    assert targets[0].app_id == "730"
    assert targets[0].active is True
    assert targets[1].active is False
    assert "appreviews/730" in url
    assert "cursor=AoIIPw%3D%3D" in url


def test_fetch_app_reviews_paginates_until_empty_and_sanitizes_raw(tmp_path):
    session = FakeSession(
        [
            FakeResponse(payload=steam_payload(cursor="cursor-2", reviews=[steam_review("1001")])),
            FakeResponse(payload=steam_payload(cursor="", reviews=[])),
        ]
    )

    reports = fetch_app_reviews(
        SteamApp("730", "Counter-Strike 2", True, None),
        tmp_path,
        max_pages_per_app=5,
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    assert len(reports) == 2
    assert reports[0]["status"] == "fetched"
    assert reports[0]["review_count"] == 1
    assert reports[1]["status"] == "empty"
    assert reports[1]["terminal_reason"] == "empty_page"
    assert session.calls[0]["params"]["cursor"] == "*"
    assert session.calls[1]["params"]["cursor"] == "cursor-2"
    saved_payload = json.loads(Path(reports[0]["raw_json_path"]).read_text(encoding="utf-8"))
    assert "steamid" not in saved_payload["reviews"][0]["author"]


def test_fetch_app_reviews_retries_rate_limited_page(tmp_path):
    sleeps = []
    session = FakeSession(
        [
            FakeResponse(status_code=429, payload={}),
            FakeResponse(payload=steam_payload(cursor="", reviews=[steam_review("1001")])),
        ]
    )

    reports = fetch_app_reviews(
        SteamApp("730", "Counter-Strike 2", True, None),
        tmp_path,
        max_pages_per_app=1,
        max_attempts=2,
        retry_delay_seconds=0.25,
        session=session,
        sleep_fn=sleeps.append,
    )

    assert len(session.calls) == 2
    assert sleeps == [0.25]
    assert reports[0]["status"] == "fetched"
    assert reports[0]["attempt_count"] == 2
    assert reports[0]["terminal_reason"] == "page_cap_reached"


def test_sanitize_payload_removes_steam_user_ids():
    payload = steam_payload(reviews=[steam_review()])

    sanitized = sanitize_payload_for_storage(payload)

    assert "steamid" in payload["reviews"][0]["author"]
    assert "steamid" not in sanitized["reviews"][0]["author"]


def test_load_steam_pipeline_is_idempotent_and_updates_changed_reviews(tmp_path):
    targets_path = tmp_path / "targets" / "steam_apps.csv"
    write_targets(targets_path)
    db_path = tmp_path / "steam_reviews.sqlite"
    raw_dir = tmp_path / "raw" / "run-steam"
    raw_dir.mkdir(parents=True)
    payload_path = raw_dir / "app_730_page_0001.json"
    write_json(payload_path, sanitize_payload_for_storage(steam_payload(cursor="", reviews=[steam_review("1001", "Original text", updated=1)])))
    write_jsonl(
        raw_dir / "review_pages.jsonl",
        [
            {
                "run_id": "run-steam",
                "app_id": "730",
                "app_name": "Counter-Strike 2",
                "page_number": 1,
                "request_url": "https://store.steampowered.com/appreviews/730",
                "cursor": "*",
                "next_cursor": "",
                "status": "fetched",
                "status_code": 200,
                "fetched_at": "2026-06-15T00:00:00+00:00",
                "raw_json_path": str(payload_path),
                "response_bytes": 10,
                "review_count": 1,
                "total_reviews": 1,
                "total_positive": 1,
                "total_negative": 0,
                "attempt_count": 1,
                "error_message": None,
                "terminal_reason": "missing_next_cursor",
            }
        ],
    )

    first = load_pipeline_run(db_path, raw_dir, targets_path)
    second = load_pipeline_run(db_path, raw_dir, targets_path)
    write_json(payload_path, sanitize_payload_for_storage(steam_payload(cursor="", reviews=[steam_review("1001", "Edited text", updated=2)])))
    third = load_pipeline_run(db_path, raw_dir, targets_path)
    report = validate_database(db_path)
    export_path = tmp_path / "exports" / "steam_reviews.csv"
    export_summary = export_reviews(db_path, export_path, "csv")

    assert first["reviews_inserted"] == 1
    assert first["reviews_updated"] == 0
    assert second["reviews_inserted"] == 0
    assert second["duplicates_skipped"] == 1
    assert third["reviews_updated"] == 1
    assert report["counts"]["apps"] == 1
    assert report["counts"]["review_pages"] == 1
    assert report["counts"]["reviews"] == 1
    assert report["quality"]["missing_review_text"] == 0
    assert export_summary["review_count"] == 1
    assert "Edited text" in export_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(steam_reviews)")}
    assert "steamid" not in columns


def test_load_targets_rejects_bad_app_id(tmp_path):
    path = tmp_path / "steam_apps.csv"
    path.write_text("app_id,app_name,active,notes\nnot-an-id,Bad,true,\n", encoding="utf-8")

    with pytest.raises(ValueError, match="app_id must be numeric"):
        load_targets(path)
