from __future__ import annotations

from pathlib import Path

from amazon_review_pipeline.fetch_cache import resolve_metadata_html_path
from amazon_review_pipeline.parser import parse_top_reviews


def extract_reviews_from_raw_dir(raw_dir: Path, metadata_by_target: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    all_reviews: list[dict] = []
    target_reports = []
    if metadata_by_target:
        target_ids = sorted(metadata_by_target)
    else:
        target_ids = [html_path.stem for html_path in sorted(raw_dir.glob("*.html"))]

    for target_id in target_ids:
        metadata = metadata_by_target.get(target_id, {})
        html_path = resolve_metadata_html_path(metadata, raw_dir, target_id)
        source_url = metadata.get("final_url") or metadata.get("requested_url") or ""
        if html_path:
            html = html_path.read_text(encoding="utf-8")
            reviews = parse_top_reviews(html, source_url=source_url, target_id=target_id)
            html_path_value = str(html_path)
        else:
            reviews = []
            html_path_value = None
        all_reviews.extend(reviews)
        target_reports.append(
            {
                "target_id": target_id,
                "html_path": html_path_value,
                "source_url": source_url,
                "blocked_or_signin": bool(metadata.get("blocked_or_signin")),
                "review_count": len(reviews),
                "non_empty_bodies": sum(1 for review in reviews if review.get("body")),
            }
        )
    return all_reviews, target_reports
