import csv
from pathlib import Path

import pytest

from app_store_source_evaluation.smoke import build_smoke_report, scan_storefront_html
from app_store_source_evaluation.targets import active_targets, load_public_app_targets


def write_app_store_targets(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "app_name",
                "category",
                "google_play_package",
                "apple_app_id",
                "apple_slug",
                "active",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "app_name": "ChatGPT",
                "category": "ai_tools",
                "google_play_package": "com.openai.chatgpt",
                "apple_app_id": "6448311069",
                "apple_slug": "chatgpt",
                "active": "true",
                "notes": "fixture",
            }
        )
        writer.writerow(
            {
                "app_name": "Inactive App",
                "category": "test",
                "google_play_package": "com.example.inactive",
                "apple_app_id": "123456789",
                "apple_slug": "inactive-app",
                "active": "false",
                "notes": "",
            }
        )


def test_load_public_app_targets_and_build_urls(tmp_path):
    path = tmp_path / "targets" / "app_store_public_apps.csv"
    write_app_store_targets(path)

    targets = load_public_app_targets(path)
    active = active_targets(targets)

    assert len(targets) == 2
    assert len(active) == 1
    assert active[0].app_name == "ChatGPT"
    assert active[0].google_play_url == "https://play.google.com/store/apps/details?id=com.openai.chatgpt&hl=en_US&gl=US"
    assert active[0].apple_app_store_url == "https://apps.apple.com/us/app/chatgpt/id6448311069"


def test_load_public_app_targets_rejects_invalid_apple_id(tmp_path):
    path = tmp_path / "targets" / "bad.csv"
    write_app_store_targets(path)
    text = path.read_text(encoding="utf-8").replace("6448311069", "not-an-id")
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid apple_app_id"):
        load_public_app_targets(path)


def test_scan_storefront_html_detects_review_and_access_control_markers():
    html = """
    <html>
      <script type="application/ld+json">{"aggregateRating": {"ratingValue": "4.8"}}</script>
      <body>Ratings and Reviews. 5 stars. See all reviews.</body>
    </html>
    """

    scan = scan_storefront_html(html)

    assert scan["has_review_marker"] is True
    assert scan["has_rating_marker"] is True
    assert scan["has_pagination_marker"] is True
    assert scan["has_structured_data_marker"] is True
    assert scan["has_access_control_marker"] is False

    blocked = scan_storefront_html("<html>captcha verify you are human</html>")

    assert blocked["has_access_control_marker"] is True
    assert "do not retry" in blocked["notes"]


def test_build_smoke_report_summarizes_platforms(tmp_path):
    report = build_smoke_report(tmp_path / "targets.csv", [])

    assert report["targets_path"].endswith("targets.csv")
    assert report["summary"] == {}
    assert "hidden review endpoints" in report["ethical_boundary"]
