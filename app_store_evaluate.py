from __future__ import annotations

import argparse
import json
from pathlib import Path

from app_store_source_evaluation.smoke import run_storefront_smoke
from app_store_source_evaluation.targets import active_targets, load_public_app_targets


DEFAULT_TARGETS = Path("data/targets/app_store_public_apps.csv")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate public app-store review source feasibility.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    targets = subparsers.add_parser("targets", help="Summarize the curated app-store public target set.")
    targets.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    targets.set_defaults(func=command_targets)

    smoke = subparsers.add_parser("smoke", help="Run a conservative no-login public storefront smoke test.")
    smoke.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    smoke.add_argument("--output", type=Path, required=True)
    smoke.add_argument("--limit", type=int, default=None)
    smoke.add_argument("--timeout-seconds", type=float, default=20)
    smoke.add_argument("--delay-seconds", type=float, default=1)
    smoke.set_defaults(func=command_smoke)

    args = parser.parse_args()
    return args.func(args)


def command_targets(args: argparse.Namespace) -> int:
    targets = load_public_app_targets(args.targets)
    active = active_targets(targets)
    categories = sorted({target.category for target in active})
    print(
        json.dumps(
            {
                "targets_path": str(args.targets),
                "target_count": len(targets),
                "active_target_count": len(active),
                "categories": categories,
                "platforms": ["google_play", "apple_app_store"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def command_smoke(args: argparse.Namespace) -> int:
    report = run_storefront_smoke(
        args.targets,
        args.output,
        limit=args.limit,
        timeout_seconds=args.timeout_seconds,
        delay_seconds=args.delay_seconds,
    )
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
