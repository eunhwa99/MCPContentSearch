from __future__ import annotations

import json

from evals.contextwiki_eval import run_contextwiki_eval


def main() -> None:
    print(json.dumps(run_contextwiki_eval(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
