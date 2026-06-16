import csv
import json
import os
import sqlite3
from pathlib import Path

import psycopg
import pytest

from steam_review_pipeline.database import export_reviews, load_pipeline_run, validate_database
from steam_review_pipeline.fetcher import build_review_url, fetch_app_reviews, sanitize_payload_for_storage
from steam_review_pipeline.files import write_json, write_jsonl
from steam_review_pipeline.models import SteamApp
from steam_review_pipeline.postgres_database import (
    app_high_water_marks,
    app_sync_states,
    load_pipeline_run_postgres,
    update_app_sync_states,
    validate_postgres,
)
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
            "personaname": "example-user",
            "profile_url": "https://steamcommunity.com/profiles/76561198000000000/",
            "avatar": "avatar-hash",
            "persona_status": "online",
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
    assert "profile_url" not in saved_payload["reviews"][0]["author"]
    assert "personaname" not in saved_payload["reviews"][0]["author"]
    assert saved_payload["reviews"][0]["author"] == {
        "last_played": 1700000000,
        "playtime_at_review": 90,
        "playtime_forever": 120,
        "playtime_last_two_weeks": 5,
    }


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


def test_fetch_app_reviews_stops_when_updated_pages_are_caught_up(tmp_path):
    session = FakeSession(
        [
            FakeResponse(payload=steam_payload(cursor="cursor-2", reviews=[steam_review("1001", updated=9), steam_review("1002", updated=8)])),
            FakeResponse(payload=steam_payload(cursor="cursor-3", reviews=[steam_review("1003", updated=5), steam_review("1004", updated=4)])),
        ]
    )

    reports = fetch_app_reviews(
        SteamApp("730", "Counter-Strike 2", True, None),
        tmp_path,
        max_pages_per_app=10,
        high_water_timestamp=5,
        session=session,
        sleep_fn=lambda _seconds: None,
    )

    assert len(reports) == 2
    assert reports[0]["terminal_reason"] is None
    assert reports[1]["terminal_reason"] == "caught_up_to_existing_reviews"
    assert session.calls[1]["params"]["cursor"] == "cursor-2"


def test_sanitize_payload_removes_steam_user_ids():
    payload = steam_payload(reviews=[steam_review()])

    sanitized = sanitize_payload_for_storage(payload)

    assert "steamid" in payload["reviews"][0]["author"]
    assert "profile_url" in payload["reviews"][0]["author"]
    assert "steamid" not in sanitized["reviews"][0]["author"]
    assert "profile_url" not in sanitized["reviews"][0]["author"]
    assert "personaname" not in sanitized["reviews"][0]["author"]
    assert "avatar" not in sanitized["reviews"][0]["author"]
    assert "persona_status" not in sanitized["reviews"][0]["author"]
    assert set(sanitized["reviews"][0]["author"]) == {
        "last_played",
        "playtime_at_review",
        "playtime_forever",
        "playtime_last_two_weeks",
    }


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


def postgres_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


def reset_postgres(database_url: str):
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            DROP TABLE IF EXISTS
                steam_app_sync_state,
                steam_review_changes,
                steam_reviews,
                steam_review_pages,
                steam_apps,
                steam_runs
            CASCADE
            """
        )
        connection.commit()


def test_postgres_load_is_idempotent_tracks_changes_and_high_water(tmp_path):
    database_url = postgres_url()
    reset_postgres(database_url)
    targets_path = tmp_path / "targets" / "steam_apps.csv"
    write_targets(targets_path)
    raw_dir = tmp_path / "raw" / "run-postgres"
    raw_dir.mkdir(parents=True)
    payload_path = raw_dir / "app_730_page_0001.json"
    write_json(payload_path, sanitize_payload_for_storage(steam_payload(cursor="", reviews=[steam_review("1001", "Original text", updated=1)])))
    write_jsonl(
        raw_dir / "review_pages.jsonl",
        [
            {
                "run_id": "run-postgres",
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
                "max_timestamp_updated": 1,
                "min_timestamp_updated": 1,
                "attempt_count": 1,
                "error_message": None,
                "terminal_reason": "missing_next_cursor",
            }
        ],
    )

    first = load_pipeline_run_postgres(database_url, raw_dir, targets_path)
    second = load_pipeline_run_postgres(database_url, raw_dir, targets_path)
    write_json(payload_path, sanitize_payload_for_storage(steam_payload(cursor="", reviews=[steam_review("1001", "Edited text", updated=2)])))
    page_reports = json.loads((raw_dir / "review_pages.jsonl").read_text(encoding="utf-8").strip())
    page_reports["max_timestamp_updated"] = 2
    page_reports["min_timestamp_updated"] = 2
    write_jsonl(raw_dir / "review_pages.jsonl", [page_reports])
    third = load_pipeline_run_postgres(database_url, raw_dir, targets_path)
    sync_summary = update_app_sync_states(
        database_url,
        [page_reports],
        "run-postgres",
        "updated",
        "2026-06-15T00:00:00+00:00",
        "2026-06-15T00:01:00+00:00",
    )
    report = validate_postgres(database_url)
    high_water = app_high_water_marks(database_url, ["730", "999"])
    sync_states = app_sync_states(database_url, ["730", "999"])

    assert first["reviews_inserted"] == 1
    assert first["reviews_updated"] == 0
    assert second["reviews_inserted"] == 0
    assert second["duplicates_skipped"] == 1
    assert third["reviews_updated"] == 1
    assert sync_summary["complete_apps"] == ["730"]
    assert sync_summary["backlogged_apps"] == []
    assert report["counts"]["apps"] == 1
    assert report["counts"]["review_pages"] == 1
    assert report["counts"]["reviews"] == 1
    assert report["counts"]["review_changes"] == 1
    assert report["counts"]["sync_states"] == 1
    assert report["change_counts"]["updated"] == 1
    assert high_water == {"730": 2, "999": 0}
    assert sync_states["730"]["backlogged"] is False
    assert sync_states["999"]["backlogged"] is True


def test_sync_state_does_not_advance_for_capped_app(tmp_path):
    database_url = postgres_url()
    reset_postgres(database_url)
    targets_path = tmp_path / "targets" / "steam_apps.csv"
    write_targets(targets_path)
    raw_dir = tmp_path / "raw" / "run-capped"
    raw_dir.mkdir(parents=True)
    payload_path = raw_dir / "app_730_page_0001.json"
    write_json(payload_path, sanitize_payload_for_storage(steam_payload(cursor="more", reviews=[steam_review("2001", "New text", updated=10)])))
    page_report = {
        "run_id": "run-capped",
        "app_id": "730",
        "app_name": "Counter-Strike 2",
        "page_number": 1,
        "request_url": "https://store.steampowered.com/appreviews/730",
        "cursor": "*",
        "next_cursor": "more",
        "status": "fetched",
        "status_code": 200,
        "fetched_at": "2026-06-15T00:00:00+00:00",
        "raw_json_path": str(payload_path),
        "response_bytes": 10,
        "review_count": 1,
        "total_reviews": 100,
        "total_positive": 1,
        "total_negative": 0,
        "max_timestamp_updated": 10,
        "min_timestamp_updated": 10,
        "attempt_count": 1,
        "error_message": None,
        "terminal_reason": "page_cap_reached",
    }
    write_jsonl(raw_dir / "review_pages.jsonl", [page_report])

    load_pipeline_run_postgres(database_url, raw_dir, targets_path)
    sync_summary = update_app_sync_states(
        database_url,
        [page_report],
        "run-capped",
        "updated",
        "2026-06-15T00:00:00+00:00",
        "2026-06-15T00:01:00+00:00",
    )
    high_water = app_high_water_marks(database_url, ["730"])
    sync_states = app_sync_states(database_url, ["730"])

    assert sync_summary["complete_apps"] == []
    assert sync_summary["backlogged_apps"] == ["730"]
    assert high_water == {"730": 0}
    assert sync_states["730"]["backlogged"] is True


def test_load_targets_rejects_bad_app_id(tmp_path):
    path = tmp_path / "steam_apps.csv"
    path.write_text("app_id,app_name,active,notes\nnot-an-id,Bad,true,\n", encoding="utf-8")

    with pytest.raises(ValueError, match="app_id must be numeric"):
        load_targets(path)
