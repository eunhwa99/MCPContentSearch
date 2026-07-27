from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _ensure_repo_root_on_sys_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


_ensure_repo_root_on_sys_path()

from evals.contextwiki_eval import run_contextwiki_eval


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic ContextWiki evals and optionally write JSON and "
            "Markdown artifacts."
        )
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional directory where eval JSON and Markdown artifacts will be written.",
    )
    parser.add_argument(
        "--include-latency",
        action="store_true",
        help="Include non-deterministic runtime latency summaries in stdout and runtime_metrics.json.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or None
    summary = run_contextwiki_eval(
        output_dir=output_dir,
        include_latency=args.include_latency,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary.get("passed", False):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
