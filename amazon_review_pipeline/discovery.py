from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from amazon_review_pipeline.config import DEFAULT_HEADERS, DEFAULT_TARGETS, REQUIRED_TARGET_COLUMNS
from amazon_review_pipeline.fetcher import detect_blocked_or_signin
from amazon_review_pipeline.targets import infer_asin_from_url
from amazon_review_pipeline.utils import clean_text, sha256_text


BESTSELLERS_URL = "https://www.amazon.com/gp/bestsellers/?ref_=nav_em_cs_bestsellers_0_1_1_2"
AMAZON_BASE_URL = "https://www.amazon.com"
DEFAULT_DISCOVERY_ROOT = Path("data/discovery")


@dataclass(frozen=True)
class BestsellerProduct:
    rank: int
    asin: str
    target_id: str
    url: str
    product_name: str | None
    source_url: str


@dataclass(frozen=True)
class SeedPage:
    rank: int
    url: str
    label: str
    source_url: str | None = None


def fetch_bestsellers_page(url: str, timeout: float) -> requests.Response:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
    response.encoding = "utf-8"
    return response


def extract_bestseller_seed_pages(html: str, source_url: str, max_pages: int) -> list[SeedPage]:
    soup = BeautifulSoup(html, "html.parser")
    seed_pages: list[SeedPage] = [SeedPage(rank=1, url=canonical_bestseller_url(source_url), label="Best Sellers")]
    seen_urls = {seed_pages[0].url}

    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        absolute_url = urljoin(AMAZON_BASE_URL, href)
        if not is_bestseller_category_url(absolute_url):
            continue

        canonical_url = canonical_bestseller_url(absolute_url)
        if canonical_url in seen_urls:
            continue

        seen_urls.add(canonical_url)
        label = seed_label(anchor.get_text(" ", strip=True), canonical_url)
        seed_pages.append(SeedPage(rank=len(seed_pages) + 1, url=canonical_url, label=label, source_url=source_url))
        if max_pages and len(seed_pages) >= max_pages:
            break

    return seed_pages


def extract_bestseller_products(html: str, source_url: str, limit: int | None = None) -> list[BestsellerProduct]:
    soup = BeautifulSoup(html, "html.parser")
    products_by_asin: dict[str, dict] = {}
    asin_order: list[str] = []

    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        absolute_url = urljoin(AMAZON_BASE_URL, href)
        asin = infer_asin_from_url(absolute_url)
        if not asin:
            continue

        if asin not in products_by_asin:
            asin_order.append(asin)
            products_by_asin[asin] = {
                "asin": asin,
                "url": canonical_product_url(asin),
                "product_name": None,
            }

        candidate_name = title_candidate(anchor.get_text(" ", strip=True))
        if candidate_name:
            current_name = products_by_asin[asin]["product_name"]
            if not current_name or len(candidate_name) > len(current_name):
                products_by_asin[asin]["product_name"] = candidate_name

    products: list[BestsellerProduct] = []
    for rank, asin in enumerate(asin_order, start=1):
        if limit is not None and len(products) >= limit:
            break
        row = products_by_asin[asin]
        products.append(
            BestsellerProduct(
                rank=rank,
                asin=asin,
                target_id=target_id_for_asin(asin),
                url=row["url"],
                product_name=row["product_name"],
                source_url=source_url,
            )
        )
    return products


def dedupe_products(products: list[BestsellerProduct]) -> list[BestsellerProduct]:
    deduped: list[BestsellerProduct] = []
    seen_asins: set[str] = set()
    for product in products:
        if product.asin in seen_asins:
            continue
        seen_asins.add(product.asin)
        deduped.append(product)
    return deduped


def merge_products_into_targets(
    targets_path: Path,
    products: list[BestsellerProduct],
    active: bool,
    discovered_at: str,
) -> dict:
    existing_rows = read_target_rows(targets_path)
    merged: dict[str, dict] = {}

    for row in existing_rows:
        normalized = normalize_target_row(row)
        merged[normalized["target_id"]] = normalized

    added = 0
    updated = 0
    for product in products:
        if product.target_id in merged:
            row = merged[product.target_id]
            changed = False
            for key, value in {
                "asin": product.asin,
                "product_name": product.product_name,
                "category": row.get("category") or "best_sellers",
            }.items():
                if value and not clean_text(row.get(key)):
                    row[key] = value
                    changed = True
            updated += int(changed)
            continue

        merged[product.target_id] = {
            "target_id": product.target_id,
            "url": product.url,
            "asin": product.asin,
            "product_name": product.product_name or "",
            "category": "best_sellers",
            "active": "true" if active else "false",
            "notes": f"Discovered from Amazon Best Sellers rank {product.rank} on {discovered_at}",
        }
        added += 1

    rows = sorted(merged.values(), key=lambda row: row["target_id"])
    write_target_rows(targets_path, rows)
    return {"added": added, "updated": updated, "total_targets": len(rows)}


def read_target_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing_columns = set(REQUIRED_TARGET_COLUMNS).difference(reader.fieldnames or [])
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Target CSV is missing required columns: {missing}")
        return [dict(row) for row in reader]


def write_target_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REQUIRED_TARGET_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def normalize_target_row(row: dict) -> dict:
    normalized = {column: clean_text(row.get(column)) or "" for column in REQUIRED_TARGET_COLUMNS}
    asin = normalized["asin"] or infer_asin_from_url(normalized["url"]) or ""
    normalized["asin"] = asin
    if asin:
        normalized["target_id"] = target_id_for_asin(asin)
    normalized["active"] = normalize_active_value(normalized["active"])
    return normalized


def target_id_for_asin(asin: str) -> str:
    return f"amzn_{asin.lower()}"


def canonical_product_url(asin: str) -> str:
    return f"{AMAZON_BASE_URL}/dp/{asin}/"


def canonical_bestseller_url(url: str) -> str:
    parsed = urlparse(urljoin(AMAZON_BASE_URL, url))
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "gp" and parts[1] == "bestsellers":
        path = "/" + "/".join(parts[:3])
    elif len(parts) >= 2 and parts[0] == "gp" and parts[1] == "bestsellers":
        path = "/gp/bestsellers"
    else:
        path = parsed.path.rstrip("/") or "/gp/bestsellers"
    return f"{AMAZON_BASE_URL}{path}/"


def is_bestseller_category_url(url: str) -> bool:
    parsed = urlparse(urljoin(AMAZON_BASE_URL, url))
    if parsed.netloc and parsed.netloc != "www.amazon.com":
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 3 and parts[0] == "gp" and parts[1] == "bestsellers"


def seed_label(anchor_text: str | None, url: str) -> str:
    text = clean_text(anchor_text)
    if text and text.lower() != "see more":
        return text[:120]
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[2] if len(parts) >= 3 else "best_sellers"


def discovery_html_name(seed_page: SeedPage) -> str:
    parts = [part for part in urlparse(seed_page.url).path.split("/") if part]
    slug = "_".join(parts[2:]) or "root"
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", slug).strip("_") or "root"
    return f"{seed_page.rank:02d}_{slug}.html"


def title_candidate(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if len(text) < 8:
        return None
    if re.fullmatch(r"\$[\d,.]+(?:\s*-\s*\$[\d,.]+)?", text):
        return None
    if re.search(r"\d+(?:\.\d+)?\s*out\s+of\s+5\s+stars", text, re.I):
        return None
    return text[:240]


def normalize_active_value(value: str) -> str:
    return "true" if value.strip().lower() in {"true", "t", "yes", "y", "1"} else "false"


def run_discovery(args: argparse.Namespace) -> dict:
    discovered_at = datetime.now(timezone.utc).date().isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    discovery_dir = args.discovery_root / run_id
    discovery_dir.mkdir(parents=True, exist_ok=True)

    seed_pages_path = discovery_dir / "seed_pages.jsonl"
    products_path = discovery_dir / "bestseller_products.jsonl"
    report_path = discovery_dir / "discovery_report.json"

    root_response = fetch_bestsellers_page(args.seed_url, args.timeout)
    root_html = root_response.content.decode("utf-8", errors="replace")
    root_blocked = detect_blocked_or_signin(root_html, root_response.status_code)
    root_seed = SeedPage(rank=1, url=canonical_bestseller_url(root_response.url), label="Best Sellers")
    root_html_path = discovery_dir / discovery_html_name(root_seed)
    root_html_path.write_text(root_html, encoding="utf-8")

    seed_pages = [root_seed] if root_blocked else extract_bestseller_seed_pages(root_html, root_response.url, args.max_seed_pages)
    max_products_per_page = None if args.max_products_per_page == 0 else args.max_products_per_page
    page_reports: list[dict] = []
    all_products: list[BestsellerProduct] = []

    for index, seed_page in enumerate(seed_pages):
        if index == 0:
            response = root_response
            html = root_html
            html_path = root_html_path
        else:
            if args.delay > 0:
                time.sleep(args.delay)
            response = fetch_bestsellers_page(seed_page.url, args.timeout)
            html = response.content.decode("utf-8", errors="replace")
            html_path = discovery_dir / discovery_html_name(seed_page)
            html_path.write_text(html, encoding="utf-8")

        blocked_or_signin = detect_blocked_or_signin(html, response.status_code)
        products = [] if blocked_or_signin else extract_bestseller_products(html, response.url, max_products_per_page)
        all_products.extend(products)
        page_reports.append(
            {
                "rank": seed_page.rank,
                "label": seed_page.label,
                "requested_url": seed_page.url,
                "final_url": response.url,
                "status_code": response.status_code,
                "blocked_or_signin": blocked_or_signin,
                "content_hash": sha256_text(html),
                "html_path": str(html_path),
                "discovered_products": len(products),
            }
        )

    products = dedupe_products(all_products)
    merge_summary = merge_products_into_targets(args.targets, products, active=not args.inactive, discovered_at=discovered_at)

    with seed_pages_path.open("w", encoding="utf-8") as handle:
        for seed_page in seed_pages:
            handle.write(json.dumps(seed_page.__dict__, ensure_ascii=False, sort_keys=True) + "\n")

    with products_path.open("w", encoding="utf-8") as handle:
        for product in products:
            handle.write(json.dumps(product.__dict__, ensure_ascii=False, sort_keys=True) + "\n")

    report = {
        "run_id": run_id,
        "seed_url": args.seed_url,
        "max_seed_pages": args.max_seed_pages,
        "max_products_per_page": args.max_products_per_page,
        "delay": args.delay,
        "seed_pages_path": str(seed_pages_path),
        "products_path": str(products_path),
        "targets_path": str(args.targets),
        "seed_pages_discovered": len(seed_pages),
        "pages_fetched": len(page_reports),
        "blocked_pages": sum(1 for page in page_reports if page["blocked_or_signin"]),
        "discovered_products": len(products),
        "merge_summary": merge_summary,
        "page_reports": page_reports,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover Amazon Best Sellers product links and merge them into the target CSV.")
    parser.add_argument("--seed-url", default=BESTSELLERS_URL, help="Amazon Best Sellers root URL to fetch before category discovery.")
    parser.add_argument("--url", dest="seed_url", default=argparse.SUPPRESS, help="Compatibility alias for --seed-url.")
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS, help="Target CSV to update.")
    parser.add_argument("--max-seed-pages", type=int, default=12, help="Maximum Best Sellers pages to fetch, including the root page.")
    parser.add_argument("--max-products-per-page", type=int, default=0, help="Maximum unique product ASINs to extract per Best Sellers page. Use 0 for all found products.")
    parser.add_argument("--limit", dest="max_products_per_page", type=int, default=argparse.SUPPRESS, help="Compatibility alias for --max-products-per-page.")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay in seconds between discovered Best Sellers page requests.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Request timeout in seconds.")
    parser.add_argument("--discovery-root", type=Path, default=DEFAULT_DISCOVERY_ROOT, help="Directory for discovery HTML and reports.")
    parser.add_argument("--inactive", action="store_true", help="Write newly discovered targets as inactive.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        report = run_discovery(args)
    except (OSError, ValueError, requests.RequestException) as exc:
        print(f"error: {exc}")
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["blocked_pages"]:
        print("warning: Amazon returned a blocked, robot-check, or sign-in page.")
    return 0
