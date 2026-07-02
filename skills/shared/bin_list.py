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

try:
    import bin_store
except ImportError as e:
    print(json.dumps({"error": f"bin_store unavailable: {e}"}))
    sys.exit(1)

items = [
    bin_store.to_public(i)
    for i in bin_store.list_items(
        q=args.get("q") or None,
        kind=args.get("kind") or None,
        limit=int(args.get("limit", 25)),
    )
]
print(json.dumps({"count": len(items), "items": items}, default=str))
