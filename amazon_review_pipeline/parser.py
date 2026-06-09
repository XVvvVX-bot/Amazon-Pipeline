from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from amazon_review_pipeline.utils import clean_text


def parse_top_reviews(html: str, source_url: str, target_id: str | None = None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    containers = find_review_containers(soup)

    reviews: list[dict] = []
    for container in containers:
        if not isinstance(container, Tag):
            continue
        review_date = text_or_none(container.select_one('[data-hook="review-date"]'))
        reviews.append(
            {
                "target_id": target_id,
                "review_id": parse_review_id(container),
                "asin": container.get("data-asin"),
                "reviewer_name": text_or_none(container.select_one(".a-profile-name")),
                "rating": parse_rating(parse_rating_text(container)),
                "title": parse_review_title(container),
                "review_date": review_date,
                "variation": parse_variation(container),
                "verified_purchase": container.select_one('[data-hook="avp-badge"]') is not None,
                "helpful_votes": parse_helpful_votes(text_or_none(container.select_one('[data-hook="helpful-vote-statement"]'))),
                "body": parse_review_body(container),
                "source_url": source_url,
            }
        )
    return reviews


def detect_review_section(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return bool(find_review_containers(soup))


def find_review_containers(soup: BeautifulSoup) -> list[Tag]:
    selectors = (
        "#localTopReviewsList [data-hook='reviewContainer']",
        "#localTopReviewsList [data-hook='review']",
        "#cm-cr-dp-review-list [data-hook='review']",
        "#customerReviews [data-hook='review']",
        "[data-hook='reviewContainer']",
        "[data-hook='review']",
        "div[id^='customer_review-']",
    )
    containers: list[Tag] = []
    seen: set[int] = set()
    for selector in selectors:
        for element in soup.select(selector):
            if not isinstance(element, Tag):
                continue
            marker = id(element)
            if marker in seen:
                continue
            seen.add(marker)
            containers.append(element)
        if containers:
            break
    return containers


def parse_review_id(container: Tag) -> str | None:
    review_id = container.get("data-reviewid")
    if review_id:
        return str(review_id)
    element_id = container.get("id")
    if isinstance(element_id, str) and element_id.startswith("customer_review-"):
        return element_id.removeprefix("customer_review-")
    return None


def parse_rating_text(container: Tag) -> str | None:
    selectors = (
        '[data-hook="review-star-rating"] .a-icon-alt',
        '[data-hook="cmps-review-star-rating"] .a-icon-alt',
        '[data-hook="review-title"] .a-icon-alt',
        '[data-hook="reviewTitle"] .a-icon-alt',
        ".review-rating .a-icon-alt",
        ".a-icon-alt",
    )
    for selector in selectors:
        value = text_or_none(container.select_one(selector))
        if value and "star" in value.lower():
            return value
    return None


def parse_review_title(container: Tag) -> str | None:
    title = first_text(
        container,
        (
            '[data-hook="reviewTitle"]',
            '[data-hook="review-title"]',
            ".review-title",
        ),
    )
    rating_text = parse_rating_text(container)
    if title and rating_text and title.startswith(rating_text):
        title = clean_text(title[len(rating_text) :])
    return title


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
    return first_text(
        container,
        (
            '[data-hook="reviewText"]',
            '[data-hook="review-body"]',
            ".review-text",
        ),
    )


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


def first_text(container: Tag, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        value = text_or_none(container.select_one(selector))
        if value:
            return value
    return None
