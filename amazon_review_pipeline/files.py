from __future__ import annotations

import json
import shutil
from pathlib import Path


def write_reviews(reviews: list[dict], output_path: Path) -> None:
    write_jsonl(reviews, output_path)


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_fetch_metadata(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    metadata: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            metadata[row["target_id"]] = row
    return metadata


def infer_run_id(raw_dir: Path, metadata_by_target: dict[str, dict]) -> str:
    run_ids = {row.get("run_id") for row in metadata_by_target.values() if row.get("run_id")}
    if len(run_ids) == 1:
        return str(next(iter(run_ids)))
    return raw_dir.name


def resolve_raw_dir(raw_dir: Path) -> Path:
    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw directory does not exist: {raw_dir}")
    return raw_dir


def update_latest_dir(source_dir: Path, latest_dir: Path) -> None:
    if latest_dir.exists():
        if latest_dir.is_dir():
            shutil.rmtree(latest_dir)
        else:
            latest_dir.unlink()
    shutil.copytree(source_dir, latest_dir)

