from __future__ import annotations

from amazon_review_pipeline.cli import build_parser, main
from amazon_review_pipeline.commands import command_daily, command_export, command_fetch, command_load, command_parse, command_run, command_validate, fetch_summary
from amazon_review_pipeline.config import (
    BLOCK_MARKERS,
    DEFAULT_DB_PATH,
    DEFAULT_HEADERS,
    DEFAULT_PARSED_ROOT,
    DEFAULT_RAW_ROOT,
    DEFAULT_TARGETS,
    REQUIRED_TARGET_COLUMNS,
)
from amazon_review_pipeline.fetcher import (
    detect_blocked_or_signin,
    extract_page_title,
    extract_product_title,
    fetch_target,
)
from amazon_review_pipeline.database import (
    connect_database,
    export_reviews,
    initialize_database,
    load_pipeline_run,
    stable_review_key,
    validate_database,
)
from amazon_review_pipeline.daily import (
    DEFAULT_REPORTS_ROOT,
    DEFAULT_STATE_PATH,
    apply_fetch_metadata_to_state,
    chunk_targets,
    load_pipeline_state,
    run_daily_pipeline,
    save_pipeline_state,
    select_due_targets,
)
from amazon_review_pipeline.files import (
    infer_run_id,
    load_fetch_metadata,
    resolve_raw_dir,
    update_latest_dir,
    write_jsonl,
    write_reviews,
)
from amazon_review_pipeline.models import Target
from amazon_review_pipeline.parser import (
    parse_helpful_votes,
    parse_rating,
    parse_review_body,
    parse_top_reviews,
    parse_variation,
    text_or_none,
)
from amazon_review_pipeline.targets import infer_asin_from_url, load_targets, parse_bool
from amazon_review_pipeline.utils import clean_text, make_run_id, sha256_text, utc_timestamp


if __name__ == "__main__":
    raise SystemExit(main())
