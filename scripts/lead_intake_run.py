#!/usr/bin/env python3
"""Run a lead/review intake sync. Wired to baza-lead-intake.timer."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "dashboard"))

from lead_intake import sync, _ensure_tables  # noqa: E402

if __name__ == "__main__":
    _ensure_tables()
    full = "--full" in sys.argv
    res = sync(full=full)
    print(f"[lead_intake] leads_new={res['leads_new']} "
          f"reviews_new={res['reviews_new']} errors={len(res['errors'])}")
    for e in res["errors"]:
        print(f"[lead_intake]   skip: {e}")
