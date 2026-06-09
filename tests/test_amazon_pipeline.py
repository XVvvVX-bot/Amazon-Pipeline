import json
from pathlib import Path

import pytest

from amazon_review_pipeline.commands import command_fetch, command_parse
from amazon_review_pipeline.fetcher import detect_blocked_or_signin, fetch_target
from amazon_review_pipeline.models import Target
from amazon_review_pipeline.parser import parse_top_reviews
from amazon_review_pipeline.targets import infer_asin_from_url, load_targets
from amazon_review_pipeline.utils import sha256_text


SAVED_IPAD_HTML = Path("tests/fixtures/ipad_top_reviews.html")


def write_targets(path: Path, rows: list[dict]) -> None:
    header = "target_id,url,asin,product_name,category,active,notes\n"
    lines = [
        ",".join(
            [
                row.get("target_id", ""),
                row.get("url", ""),
                row.get("asin", ""),
                row.get("product_name", ""),
                row.get("category", ""),
                row.get("active", ""),
                row.get("notes", ""),
            ]
        )
        for row in rows
    ]
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def test_load_targets_valid_inactive_and_inferred_asin(tmp_path):
    targets_path = tmp_path / "targets.csv"
    write_targets(
        targets_path,
        [
            {
                "target_id": "ipad",
                "url": "https://www.amazon.com/Apple-iPad/dp/B0DZ75TN5F/",
                "asin": "",
                "product_name": "iPad",
                "category": "tablet",
                "active": "true",
                "notes": "active row",
            },
            {
                "target_id": "inactive",
                "url": "https://www.amazon.com/Example/dp/B012345678/",
                "asin": "B012345678",
                "active": "false",
            },
        ],
    )

    targets = load_targets(targets_path)

    assert len(targets) == 2
    assert targets[0].asin == "B0DZ75TN5F"
    assert targets[0].active is True
    assert targets[1].active is False


def test_load_targets_missing_url_errors(tmp_path):
    targets_path = tmp_path / "targets.csv"
    write_targets(targets_path, [{"target_id": "bad", "url": "", "active": "true"}])

    with pytest.raises(ValueError, match="url is required"):
        load_targets(targets_path)


def test_infer_asin_from_url():
    assert infer_asin_from_url("https://www.amazon.com/Anything/dp/B0DZ75TN5F/") == "B0DZ75TN5F"


def test_parser_extracts_saved_ipad_top_reviews():
    html = SAVED_IPAD_HTML.read_text(encoding="utf-8")

    reviews = parse_top_reviews(html, "https://example.test/ipad", target_id="ipad")

    assert len(reviews) == 8
    assert all(review["body"] for review in reviews)
    assert reviews[0]["review_id"] == "R1D1X8LOJADBXT"
    assert reviews[0]["rating"] == 5.0
    assert reviews[0]["target_id"] == "ipad"


def test_blocked_signin_detection():
    html = Path("tests/fixtures/blocked_signin.html").read_text(encoding="utf-8")

    assert detect_blocked_or_signin(html, 200) is True


def test_parse_command_defaults_to_report_without_jsonl(tmp_path):
    raw_dir = tmp_path / "raw"
    parsed_root = tmp_path / "parsed"
    raw_dir.mkdir()
    (raw_dir / "ipad.html").write_text(SAVED_IPAD_HTML.read_text(encoding="utf-8"), encoding="utf-8")
    (raw_dir / "fetch_metadata.jsonl").write_text(
        '{"target_id":"ipad","requested_url":"https://www.amazon.com/Apple-iPad-11-inch-Display-All-Day/dp/B0DZ75TN5F/","final_url":"https://www.amazon.com/Apple-iPad-11-inch-Display-All-Day/dp/B0DZ75TN5F/","blocked_or_signin":false}\n',
        encoding="utf-8",
    )

    result = command_parse(type("Args", (), {"raw_dir": raw_dir, "parsed_root": parsed_root})())

    reviews_path = parsed_root / raw_dir.name / "reviews.jsonl"
    report_path = parsed_root / raw_dir.name / "parse_report.json"
    assert result == 0
    assert not reviews_path.exists()
    assert report_path.exists()
    assert '"keep_jsonl": false' in report_path.read_text(encoding="utf-8")


def test_parse_command_keeps_jsonl_when_requested(tmp_path):
    raw_dir = tmp_path / "raw"
    parsed_root = tmp_path / "parsed"
    raw_dir.mkdir()
    (raw_dir / "ipad.html").write_text(SAVED_IPAD_HTML.read_text(encoding="utf-8"), encoding="utf-8")
    (raw_dir / "fetch_metadata.jsonl").write_text(
        '{"target_id":"ipad","requested_url":"https://www.amazon.com/Apple-iPad-11-inch-Display-All-Day/dp/B0DZ75TN5F/","final_url":"https://www.amazon.com/Apple-iPad-11-inch-Display-All-Day/dp/B0DZ75TN5F/","blocked_or_signin":false}\n',
        encoding="utf-8",
    )

    result = command_parse(type("Args", (), {"raw_dir": raw_dir, "parsed_root": parsed_root, "keep_jsonl": True})())

    reviews_path = parsed_root / raw_dir.name / "reviews.jsonl"
    assert result == 0
    assert reviews_path.exists()
    assert len(reviews_path.read_text(encoding="utf-8").splitlines()) == 8


def test_fetch_command_reuses_existing_successful_raw_page(tmp_path, monkeypatch):
    raw_root = tmp_path / "raw"
    previous_run = raw_root / "previous-run"
    previous_run.mkdir(parents=True)
    previous_html = previous_run / "ipad.html"
    previous_html.write_text("<html><title>cached</title></html>", encoding="utf-8")
    (previous_run / "fetch_metadata.jsonl").write_text(
        (
            '{"run_id":"previous-run","target_id":"ipad","asin":"B0DZ75TN5F",'
            '"requested_url":"https://www.amazon.com/dp/B0DZ75TN5F/",'
            '"final_url":"https://www.amazon.com/dp/B0DZ75TN5F/",'
            '"status":"fetched","status_code":200,"fetched_at":"2026-06-08T00:00:00+00:00",'
            f'"html_path":"{previous_html.as_posix()}","content_hash":"abc",'
            '"blocked_or_signin":false,"response_bytes":33,"page_title":"cached",'
            '"product_title":"iPad","error_message":null,"raw_storage":"stored"}\n'
        ),
        encoding="utf-8",
    )
    targets_path = tmp_path / "targets.csv"
    write_targets(
        targets_path,
        [
            {
                "target_id": "ipad",
                "url": "https://www.amazon.com/dp/B0DZ75TN5F/",
                "asin": "B0DZ75TN5F",
                "active": "true",
            }
        ],
    )

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("network fetch should not run when reusable raw page exists")

    monkeypatch.setattr("amazon_review_pipeline.commands.fetch_target", fail_fetch)

    result = command_fetch(type("Args", (), {"targets": targets_path, "raw_root": raw_root, "timeout": 1.0, "force": False})())

    metadata_files = sorted(path for path in raw_root.glob("*/fetch_metadata.jsonl") if path.parent.name not in {"previous-run", "latest"})
    assert result == 0
    assert len(metadata_files) == 1
    metadata_text = metadata_files[0].read_text(encoding="utf-8")
    metadata = json.loads(metadata_text)
    assert '"status": "reused"' in metadata_text
    assert '"raw_storage": "reused"' in metadata_text
    assert Path(metadata["html_path"]) == previous_html
    assert not (metadata_files[0].parent / "ipad.html").exists()


def test_fetch_target_deduplicates_identical_html(monkeypatch, tmp_path):
    existing_html = tmp_path / "existing.html"
    html = "<html><title>same</title><span id='productTitle'>iPad</span></html>"
    existing_html.write_text(html, encoding="utf-8")
    content_hash_index = {sha256_text(html): existing_html}

    class FakeResponse:
        content = html.encode("utf-8")
        status_code = 200
        url = "https://www.amazon.com/dp/B0DZ75TN5F/"

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("amazon_review_pipeline.fetcher.requests.Session", FakeSession)

    metadata = fetch_target(
        Target(
            target_id="ipad",
            url="https://www.amazon.com/dp/B0DZ75TN5F/",
            asin="B0DZ75TN5F",
            product_name="iPad",
            category="tablet",
            active=True,
            notes=None,
        ),
        tmp_path / "new-run",
        timeout=1.0,
        content_hash_index=content_hash_index,
    )

    assert metadata["status"] == "fetched"
    assert metadata["raw_storage"] == "deduplicated"
    assert metadata["html_path"] == str(existing_html)
    assert not (tmp_path / "new-run" / "ipad.html").exists()
