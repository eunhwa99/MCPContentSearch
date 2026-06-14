from __future__ import annotations

import argparse
import json

from evals.contextwiki_eval import run_contextwiki_eval


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic ContextWiki evals and optionally write JSON artifacts."
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional directory where eval JSON artifacts will be written.",
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
