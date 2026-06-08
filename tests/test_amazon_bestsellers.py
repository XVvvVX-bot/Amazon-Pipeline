from pathlib import Path
from types import SimpleNamespace

from amazon_review_pipeline.discovery import (
    dedupe_products,
    extract_bestseller_seed_pages,
    extract_bestseller_products,
    merge_products_into_targets,
    run_discovery,
    target_id_for_asin,
)


def test_target_id_uses_asin_not_product_name():
    assert target_id_for_asin("B0DZ75TN5F") == "amzn_b0dz75tn5f"


def test_extract_bestseller_products_dedupes_by_asin_and_keeps_duplicate_names():
    html = Path("tests/fixtures/bestsellers_sample.html").read_text(encoding="utf-8")

    products = extract_bestseller_products(html, "https://www.amazon.com/gp/bestsellers/", limit=10)

    assert [product.asin for product in products] == ["B000000001", "B000000002", "B000000003"]
    assert products[0].target_id == "amzn_b000000001"
    assert products[1].target_id == "amzn_b000000002"
    assert products[0].product_name == "Reusable Water Bottle"
    assert products[1].product_name == "Reusable Water Bottle"
    assert products[2].product_name == "Wireless Keyboard with Stand"


def test_extract_bestseller_seed_pages_discovers_category_pages():
    html = Path("tests/fixtures/bestsellers_sample.html").read_text(encoding="utf-8")

    seed_pages = extract_bestseller_seed_pages(html, "https://www.amazon.com/gp/bestsellers/", max_pages=3)

    assert [seed.url for seed in seed_pages] == [
        "https://www.amazon.com/gp/bestsellers/",
        "https://www.amazon.com/gp/bestsellers/electronics/",
        "https://www.amazon.com/gp/bestsellers/kitchen/",
    ]
    assert seed_pages[1].label == "electronics"


def test_dedupe_products_keeps_first_product_for_each_asin():
    html = Path("tests/fixtures/bestsellers_sample.html").read_text(encoding="utf-8")
    first_page = extract_bestseller_products(html, "https://www.amazon.com/gp/bestsellers/", limit=2)
    second_page = extract_bestseller_products(html, "https://www.amazon.com/gp/bestsellers/electronics/", limit=3)

    products = dedupe_products(first_page + second_page)

    assert [product.asin for product in products] == ["B000000001", "B000000002", "B000000003"]


def test_merge_rewrites_existing_name_based_target_id_and_adds_new_rows(tmp_path):
    targets = tmp_path / "amazon_products.csv"
    targets.write_text(
        "target_id,url,asin,product_name,category,active,notes\n"
        "ipad_a16_blue_128gb,https://www.amazon.com/Apple-iPad/dp/B0DZ75TN5F/,B0DZ75TN5F,iPad,tablet,true,existing\n",
        encoding="utf-8",
    )
    html = Path("tests/fixtures/bestsellers_sample.html").read_text(encoding="utf-8")
    products = extract_bestseller_products(html, "https://www.amazon.com/gp/bestsellers/", limit=1)

    summary = merge_products_into_targets(targets, products, active=True, discovered_at="2026-06-08")

    text = targets.read_text(encoding="utf-8")
    assert summary["added"] == 1
    assert "ipad_a16_blue_128gb" not in text
    assert "amzn_b0dz75tn5f" in text
    assert "amzn_b000000001" in text


def test_run_discovery_fetches_discovered_seed_pages_and_writes_outputs(tmp_path, monkeypatch):
    root_html = Path("tests/fixtures/bestsellers_sample.html").read_text(encoding="utf-8")
    category_html = """
    <html><body>
      <a href="/Category-Product/dp/B000000004/ref=zg_bs_4">Category Product</a>
      <a href="/Sample-Product-One/dp/B000000001/ref=zg_bs_dup">Duplicate Existing Product</a>
    </body></html>
    """
    responses = {
        "https://www.amazon.com/gp/bestsellers/?ref_=nav_em_cs_bestsellers_0_1_1_2": SimpleNamespace(
            content=root_html.encode("utf-8"),
            status_code=200,
            url="https://www.amazon.com/gp/bestsellers/",
        ),
        "https://www.amazon.com/gp/bestsellers/electronics/": SimpleNamespace(
            content=category_html.encode("utf-8"),
            status_code=200,
            url="https://www.amazon.com/gp/bestsellers/electronics/",
        ),
    }

    def fake_fetch(url, timeout):
        return responses[url]

    monkeypatch.setattr("amazon_review_pipeline.discovery.fetch_bestsellers_page", fake_fetch)

    report = run_discovery(
        SimpleNamespace(
            seed_url="https://www.amazon.com/gp/bestsellers/?ref_=nav_em_cs_bestsellers_0_1_1_2",
            targets=tmp_path / "amazon_products.csv",
            max_seed_pages=2,
            max_products_per_page=0,
            delay=0,
            timeout=20,
            discovery_root=tmp_path / "discovery",
            inactive=False,
        )
    )

    assert report["seed_pages_discovered"] == 2
    assert report["pages_fetched"] == 2
    assert report["discovered_products"] == 4
    assert Path(report["seed_pages_path"]).exists()
    assert Path(report["products_path"]).exists()
    assert "amzn_b000000004" in (tmp_path / "amazon_products.csv").read_text(encoding="utf-8")


def test_run_discovery_saves_blocked_root_without_adding_targets(tmp_path, monkeypatch):
    blocked_html = "<html><title>Robot Check</title><body>Enter the characters you see below</body></html>"

    def fake_fetch(url, timeout):
        return SimpleNamespace(content=blocked_html.encode("utf-8"), status_code=503, url=url)

    monkeypatch.setattr("amazon_review_pipeline.discovery.fetch_bestsellers_page", fake_fetch)

    targets = tmp_path / "amazon_products.csv"
    targets.write_text(
        "target_id,url,asin,product_name,category,active,notes\n"
        "amzn_b0dz75tn5f,https://www.amazon.com/dp/B0DZ75TN5F/,B0DZ75TN5F,iPad,tablet,true,existing\n",
        encoding="utf-8",
    )
    report = run_discovery(
        SimpleNamespace(
            seed_url="https://www.amazon.com/gp/bestsellers/",
            targets=targets,
            max_seed_pages=2,
            max_products_per_page=0,
            delay=0,
            timeout=20,
            discovery_root=tmp_path / "discovery",
            inactive=False,
        )
    )

    assert report["blocked_pages"] == 1
    assert report["discovered_products"] == 0
    assert report["merge_summary"]["total_targets"] == 1
    assert "amzn_b0dz75tn5f" in targets.read_text(encoding="utf-8")
