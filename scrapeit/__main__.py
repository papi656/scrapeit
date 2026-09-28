"""Single entry point: python -m scrapeit [--sort latest] [--count 10]

No subcommands by design. sources.yaml says *where*; the command line says *what*.
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .config import DEFAULT_DATA_MODEL_PATH, DEFAULT_SOURCES_PATH
from .runner import run_all

SORTS = ("latest", "hottest")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(prog="python -m scrapeit")
    parser.add_argument("--sort", default="latest", choices=SORTS)
    parser.add_argument("--count", type=int, default=10, help="cap, not a quota")
    parser.add_argument("--sources", default=DEFAULT_SOURCES_PATH)
    parser.add_argument("--data-model", default=DEFAULT_DATA_MODEL_PATH)
    parser.add_argument("--no-extract", action="store_true", help="skip the extraction LLM; emit raw items only")
    parser.add_argument("--no-post", action="store_true", help="stage and extract, but do not POST")
    args = parser.parse_args(argv)

    records = run_all(
        args.sort,
        args.count,
        args.sources,
        args.data_model,
        do_extract=not args.no_extract,
        do_post=not args.no_post,
    )

    ok = [r for r in records if not r.error]
    failed = [r for r in records if r.error]
    print(f"\n{len(ok)}/{len(records)} sources OK")
    if failed:
        print("failures:")
        for r in failed:
            print(f"  - {r.source_id}: {r.error}")
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    sys.exit(main())
