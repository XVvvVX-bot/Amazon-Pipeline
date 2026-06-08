from __future__ import annotations

from amazon_review_pipeline.discovery import (
    AMAZON_BASE_URL,
    BESTSELLERS_URL,
    DEFAULT_DISCOVERY_ROOT,
    BestsellerProduct,
    build_parser,
    canonical_product_url,
    extract_bestseller_products,
    fetch_bestsellers_page,
    main,
    merge_products_into_targets,
    normalize_active_value,
    normalize_target_row,
    read_target_rows,
    run_discovery,
    target_id_for_asin,
    title_candidate,
    write_target_rows,
)


if __name__ == "__main__":
    raise SystemExit(main())
