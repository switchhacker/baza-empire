#!/usr/bin/env python3
"""List files Serge dropped into the Baza Bin (Telegram file-drop inbox)."""
import json
import os
import sys

SKILL_META = {
    "category": "data",
    "summary": "List items in the Baza Bin — the Telegram file-drop inbox where Serge sends documents, photos, and files for the agents to use.",
    "when_to_use": "when asked what's in the bin, to find a file Serge recently sent, or to pick up dropped documents for processing",
    "args": {
        "q": "optional caption/filename search",
        "kind": "optional filter: photo|video|audio|document",
        "limit": "max rows, default 25",
    },
}

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(BASE_DIR, "dashboard"))

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

def _remote_fallback():
    """The bin DB lives on baza; on other hosts (phantom) read it over the
    dashboard API instead."""
    import urllib.parse
    import urllib.request
    base = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")
    params = {"limit": int(args.get("limit", 25))}
    if args.get("q"):
        params["q"] = args["q"]
    if args.get("kind"):
        params["kind"] = args["kind"]
    url = base + "/api/bin/list?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            print(r.read().decode())
    except Exception as e:
        print(json.dumps({"error": f"bin unavailable locally and dashboard unreachable: {e}"}))
        sys.exit(1)


# Local read needs both the module and the DB file (baza only); anything
# else — e.g. phantom's bootstrapped tree — goes through the dashboard API.
try:
    import bin_store
    _local_ok = os.path.exists(bin_store.bin_db_path())
except ImportError:
    _local_ok = False

if not _local_ok:
    _remote_fallback()
    sys.exit(0)

items = [
    bin_store.to_public(i)
    for i in bin_store.list_items(
        q=args.get("q") or None,
        kind=args.get("kind") or None,
        limit=int(args.get("limit", 25)),
    )
]
print(json.dumps({"count": len(items), "items": items}, default=str))
