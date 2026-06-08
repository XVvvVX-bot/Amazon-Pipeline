from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from amazon_review_pipeline.utils import clean_text


def parse_top_reviews(html: str, source_url: str, target_id: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    review_list = soup.select_one("#localTopReviewsList")
    if not review_list:
        return []

    reviews: list[dict] = []
    for container in review_list.select('[data-hook="reviewContainer"]'):
        if not isinstance(container, Tag):
            continue
        review_date = text_or_none(container.select_one('[data-hook="review-date"]'))
        reviews.append(
            {
                "target_id": target_id,
                "review_id": container.get("data-reviewid"),
                "asin": container.get("data-asin"),
                "reviewer_name": text_or_none(container.select_one(".a-profile-name")),
                "rating": parse_rating(text_or_none(container.select_one('[data-hook="review-star-rating"] .a-icon-alt'))),
                "title": text_or_none(container.select_one('[data-hook="reviewTitle"]')),
                "review_date": review_date,
                "variation": parse_variation(container),
                "verified_purchase": container.select_one('[data-hook="avp-badge"]') is not None,
                "helpful_votes": parse_helpful_votes(text_or_none(container.select_one('[data-hook="helpful-vote-statement"]'))),
                "body": parse_review_body(container),
                "source_url": source_url,
            }
        )
    return reviews


def parse_variation(container: Tag) -> str | None:
    format_strip = container.select_one('[data-hook="format-strip"]')
    if not format_strip:
        return None
    parts = [clean_text(span.get_text(" ", strip=True)) for span in format_strip.select("span")]
    parts = [part for part in parts if part]
    return " | ".join(parts) if parts else text_or_none(format_strip)


def parse_review_body(container: Tag) -> str | None:
    rich = container.select_one('[data-hook="reviewRichContentContainer"]')
    if rich:
        paragraphs = [clean_text(p.get_text(" ", strip=True)) for p in rich.select("p")]
        paragraphs = [paragraph for paragraph in paragraphs if paragraph]
        return "\n\n".join(paragraphs) if paragraphs else text_or_none(rich)
    return text_or_none(container.select_one('[data-hook="reviewText"]'))


def parse_rating(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def parse_helpful_votes(value: str | None) -> int:
    if not value:
        return 0
    lower_value = value.lower()
    if lower_value.startswith("one person found"):
        return 1
    match = re.search(r"([\d,]+)\s+people\s+found", lower_value)
    return int(match.group(1).replace(",", "")) if match else 0


def text_or_none(element: Tag | None) -> str | None:
    if not element:
        return None
    return clean_text(element.get_text(" ", strip=True))

