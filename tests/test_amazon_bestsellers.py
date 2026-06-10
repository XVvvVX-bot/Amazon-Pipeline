from pathlib import Path
from types import SimpleNamespace

from amazon_review_pipeline.discovery import (
    SeedPage,
    canonical_bestseller_url,
    dedupe_products,
    extract_navigation_seed_pages,
    extract_pagination_seed_pages,
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


def test_extract_seed_pages_supports_zgbs_department_links_and_canonical_urls():
    html = """
    <html><body>
      <a href="/Best-Sellers-Electronics/zgbs/electronics/ref=zg_bs_nav_electronics_0">Electronics</a>
      <a href="/gp/bestsellers/electronics/ref=zg_bs_electronics_sm">See More</a>
      <a href="/Best-Sellers-Kitchen-Dining-Bakeware/zgbs/kitchen/289668/ref=zg_bs_nav_kitchen_1">Bakeware</a>
    </body></html>
    """

    seed_pages = extract_bestseller_seed_pages(html, "https://www.amazon.com/gp/bestsellers/", max_pages=0)

    assert [seed.url for seed in seed_pages] == [
        "https://www.amazon.com/gp/bestsellers/",
        "https://www.amazon.com/Best-Sellers-Electronics/zgbs/electronics/",
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining-Bakeware/zgbs/kitchen/289668/",
    ]
    assert canonical_bestseller_url(
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/ref=zg_bs_pg_2_kitchen?_encoding=UTF8&pg=2"
    ) == "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/?pg=2"


def test_navigation_extraction_recurses_to_deeper_subdepartments_and_stops_at_terminal():
    department_page = SeedPage(
        rank=2,
        url="https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/",
        label="Kitchen",
        depth=1,
        page_type="department",
    )
    html = """
    <html><body>
      <a href="/Best-Sellers-Kitchen-Dining/zgbs/kitchen/ref=zg_bs_nav_kitchen_0">Kitchen</a>
      <a href="/Best-Sellers-Kitchen-Dining-Bakeware/zgbs/kitchen/289668/ref=zg_bs_nav_kitchen_1">Bakeware</a>
      <a href="/Best-Sellers-Kitchen-Dining-Cake-Pans/zgbs/kitchen/289668/12345/ref=zg_bs_nav_kitchen_2">Cake Pans</a>
      <a href="/Best-Sellers-Electronics/zgbs/electronics/ref=zg_bs_nav_electronics_0">Sibling Department</a>
    </body></html>
    """

    children = extract_navigation_seed_pages(html, department_page, max_subdepartment_depth=0)
    terminal_children = extract_navigation_seed_pages("<html><body>No deeper links</body></html>", children[-1], max_subdepartment_depth=0)

    assert [child.url for child in children] == [
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining-Bakeware/zgbs/kitchen/289668/",
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining-Cake-Pans/zgbs/kitchen/289668/12345/",
    ]
    assert [child.page_type for child in children] == ["subdepartment", "subdepartment"]
    assert terminal_children == []


def test_pagination_extraction_keeps_same_seed_hierarchy():
    kitchen_page = SeedPage(
        rank=2,
        url="https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/",
        label="Kitchen",
        depth=1,
        page_type="department",
    )
    html = """
    <html><body>
      <a href="/Best-Sellers-Kitchen-Dining/zgbs/kitchen/ref=zg_bs_pg_1_kitchen?_encoding=UTF8&pg=1">1</a>
      <a href="/Best-Sellers-Kitchen-Dining/zgbs/kitchen/ref=zg_bs_pg_2_kitchen?_encoding=UTF8&pg=2">2</a>
      <a href="/Best-Sellers-Kitchen-Dining/zgbs/kitchen/ref=zg_bs_pg_3_kitchen?_encoding=UTF8&pg=3">3</a>
      <a href="/Best-Sellers-Electronics/zgbs/electronics/ref=zg_bs_pg_2_electronics?_encoding=UTF8&pg=2">Other page</a>
    </body></html>
    """

    pages = extract_pagination_seed_pages(html, kitchen_page, max_pages_per_seed=0)

    assert [page.url for page in pages] == [
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/?pg=2",
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/?pg=3",
    ]
    assert [page.page_number for page in pages] == [2, 3]


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


def test_run_discovery_recurses_departments_subdepartments_and_pagination(tmp_path, monkeypatch):
    root_html = """
    <html><body>
      <a href="/Best-Sellers-Kitchen-Dining/zgbs/kitchen/ref=zg_bs_nav_kitchen_0">Kitchen & Dining</a>
      <a href="/gp/bestsellers/kitchen/ref=zg_bs_kitchen_sm">Duplicate Kitchen Link</a>
      <a href="/Root-Product/dp/B000000001/ref=zg_bs_1">Root Product</a>
    </body></html>
    """
    department_html = """
    <html><body>
      <a href="/Best-Sellers-Kitchen-Dining/zgbs/kitchen/ref=zg_bs_pg_2_kitchen?_encoding=UTF8&pg=2">2</a>
      <a href="/Best-Sellers-Kitchen-Dining-Bakeware/zgbs/kitchen/289668/ref=zg_bs_nav_kitchen_1">Bakeware</a>
      <a href="/Department-Product/dp/B000000002/ref=zg_bs_2">Department Product</a>
    </body></html>
    """
    department_page_2_html = """
    <html><body>
      <a href="/Department-Page-2-Product/dp/B000000003/ref=zg_bs_3">Department Page 2 Product</a>
    </body></html>
    """
    subdepartment_html = """
    <html><body>
      <a href="/Best-Sellers-Kitchen-Dining-Cake-Pans/zgbs/kitchen/289668/12345/ref=zg_bs_nav_kitchen_2">Cake Pans</a>
      <a href="/Subdepartment-Product/dp/B000000004/ref=zg_bs_4">Subdepartment Product</a>
    </body></html>
    """
    terminal_html = """
    <html><body>
      <a href="/Terminal-Product/dp/B000000005/ref=zg_bs_5">Terminal Product</a>
    </body></html>
    """
    responses = {
        "https://www.amazon.com/gp/bestsellers/": SimpleNamespace(
            content=root_html.encode("utf-8"),
            status_code=200,
            url="https://www.amazon.com/gp/bestsellers/",
        ),
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/": SimpleNamespace(
            content=department_html.encode("utf-8"),
            status_code=200,
            url="https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/",
        ),
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/?pg=2": SimpleNamespace(
            content=department_page_2_html.encode("utf-8"),
            status_code=200,
            url="https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/?pg=2",
        ),
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining-Bakeware/zgbs/kitchen/289668/": SimpleNamespace(
            content=subdepartment_html.encode("utf-8"),
            status_code=200,
            url="https://www.amazon.com/Best-Sellers-Kitchen-Dining-Bakeware/zgbs/kitchen/289668/",
        ),
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining-Cake-Pans/zgbs/kitchen/289668/12345/": SimpleNamespace(
            content=terminal_html.encode("utf-8"),
            status_code=200,
            url="https://www.amazon.com/Best-Sellers-Kitchen-Dining-Cake-Pans/zgbs/kitchen/289668/12345/",
        ),
    }
    fetched_urls = []

    def fake_fetch(url, timeout):
        fetched_urls.append(url)
        return responses[url]

    monkeypatch.setattr("amazon_review_pipeline.discovery.fetch_bestsellers_page", fake_fetch)

    report = run_discovery(
        SimpleNamespace(
            seed_url="https://www.amazon.com/gp/bestsellers/",
            targets=tmp_path / "amazon_products.csv",
            max_seed_pages=0,
            max_departments=0,
            max_subdepartment_depth=0,
            max_subdepartment_pages=0,
            max_pages_per_seed=0,
            max_products_per_page=0,
            delay=0,
            timeout=20,
            discovery_root=tmp_path / "discovery",
            inactive=False,
        )
    )

    assert fetched_urls == [
        "https://www.amazon.com/gp/bestsellers/",
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/",
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/?pg=2",
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining-Bakeware/zgbs/kitchen/289668/",
        "https://www.amazon.com/Best-Sellers-Kitchen-Dining-Cake-Pans/zgbs/kitchen/289668/12345/",
    ]
    assert report["seed_pages_discovered"] == 5
    assert report["departments_enqueued"] == 1
    assert report["subdepartment_pages_enqueued"] == 2
    assert report["pagination_pages_enqueued"] == 1
    assert report["discovered_products"] == 5
    targets_text = (tmp_path / "amazon_products.csv").read_text(encoding="utf-8")
    assert "amzn_b000000005" in targets_text


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
