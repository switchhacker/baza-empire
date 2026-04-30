#!/usr/bin/env python3
"""Vision indexer — CLI entrypoint mirroring image_indexer.py shape."""
import argparse
import sys

from dashboard.vision.db import DEFAULT_DB_PATH
from dashboard.vision.indexer import run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-classify ok rows")
    ap.add_argument("--retry-failed", action="store_true",
                    help="retry rows with status='failed' regardless of cooldown")
    ap.add_argument("--limit", type=int, default=0, help="stop after N images")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    args = ap.parse_args()

    return run(args.db, force=args.force, retry_failed=args.retry_failed,
               limit=args.limit, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
