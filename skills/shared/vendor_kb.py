#!/usr/bin/env python3
"""
Baza Empire — Vendor Knowledge Base

Normalizes OCR'd vendor names and suggests categories for ahb_receipts.

Sources merged at load time:
  1. SEED_VENDORS (hand-curated, below)
  2. Distinct store_name/vendor values already in ahb_receipts (learned from history)
  3. receipt_learn.json if present (promoted aliases from Serge's dashboard edits)

Public API:
    match_vendor(raw_name: str) -> (canonical: str, category: str, confidence: float)
    load_vendor_index() -> dict   (for inspection/tests)

Standalone usage:
    python3 vendor_kb.py "the homedepot"
"""
from __future__ import annotations

import os
import re
import json
import sqlite3
import difflib
from pathlib import Path

FRAMEWORK_DIR = Path(__file__).resolve().parent.parent.parent
DASHBOARD_DB  = FRAMEWORK_DIR / "dashboard" / "baza_projects.db"
LEARN_JSON    = Path(__file__).parent / "receipt_learn.json"

# Category labels MUST match what's already in ahb_receipts (capitalized).
SEED_VENDORS = {
    "Materials": [
        "The Home Depot", "Lowe's", "Menards", "84 Lumber", "Ace Hardware",
        "Harbor Freight", "Sherwin-Williams", "Ferguson", "Tractor Supply",
        "Grainger", "McMaster-Carr", "Rona", "Builders FirstSource",
    ],
    "Food": [
        "Redner's", "Giant", "Weis", "Wegmans", "Costco", "Sam's Club",
        "BJ's Wholesale", "Walmart Grocery", "Aldi", "Acme",
        "McDonald's", "Dunkin'", "Dunkin", "Starbucks", "Wawa",
        "Sheetz", "Subway", "Chipotle", "Taco Bell", "Burger King",
        "Wendy's", "Chick-fil-A", "Panera", "Domino's", "Pizza Hut",
        "Papa John's", "Papa Johns", "DoorDash", "Uber Eats", "Grubhub",
    ],
    "Fuel": [
        "Exxon", "ExxonMobil", "Shell", "BP", "Sunoco", "Gulf", "Citgo",
        "Mobil", "Valero", "Speedway", "Pilot", "Flying J", "Love's",
        "7-Eleven Fuel", "Liberty",
    ],
    "Tools": [
        "Snap-On", "Matco Tools", "Northern Tool", "Milwaukee Tool",
        "DeWalt Factory", "Mac Tools",
    ],
    "Office supplies": [
        "Staples", "Office Depot", "OfficeMax",
    ],
    "Clothes": [
        "Carhartt", "Dickies", "Red Wing", "Timberland PRO",
        "Work'n Gear", "Duluth Trading",
    ],
}

# Food item keywords — used only as a tiebreaker if vendor match fails.
FOOD_ITEM_HINTS = (
    "pizza", "coffee", "latte", "sandwich", "burger", "fries", "soda",
    "milk", "bread", "eggs", "cheese", "chicken", "salad",
)

# Gas stations sometimes also sell food; fuel detection takes priority
# when a gallons pattern is present (handled in receipt_ocr.py).


def _norm(s: str) -> str:
    """Lowercase, strip apostrophes + punctuation, collapse whitespace.
    Used for alias comparison — keeping apostrophes made "lowes" != "lowe's"."""
    if not s:
        return ""
    s = s.lower().strip()
    s = s.replace("'", "").replace("\u2019", "")  # strip straight + curly apostrophes
    s = re.sub(r"[^a-z0-9 &-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _collapsed(s: str) -> str:
    """Normalized form with all whitespace removed. Catches 'homedepot' == 'home depot'."""
    return _norm(s).replace(" ", "").replace("-", "")


def _load_history_vendors() -> list[tuple[str, str, int]]:
    """Return [(raw_name, most_common_category, count)] from ahb_receipts.
    Used as additional aliases + history_count for match scoring."""
    rows: list[tuple[str, str, int]] = []
    if not DASHBOARD_DB.exists():
        return rows
    try:
        conn = sqlite3.connect(str(DASHBOARD_DB))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """SELECT COALESCE(NULLIF(store_name,''), vendor) AS name,
                      category,
                      COUNT(*) AS n
                 FROM ahb_receipts
                WHERE COALESCE(NULLIF(store_name,''), vendor) IS NOT NULL
                  AND COALESCE(NULLIF(store_name,''), vendor) != ''
                GROUP BY LOWER(COALESCE(NULLIF(store_name,''), vendor)), category"""
        )
        for r in cur:
            rows.append((r["name"], r["category"] or "", int(r["n"] or 0)))
        conn.close()
    except Exception:
        pass
    return rows


def _load_learned() -> dict:
    """Load receipt_learn.json (emitted by receipt_learn.py)."""
    if not LEARN_JSON.exists():
        return {"vendor_aliases": {}, "vendor_category_overrides": {}}
    try:
        return json.loads(LEARN_JSON.read_text())
    except Exception:
        return {"vendor_aliases": {}, "vendor_category_overrides": {}}


_INDEX_CACHE: dict | None = None


def load_vendor_index() -> dict:
    """Returns:
        {
          "Home Depot": {
              "canonical": "Home Depot",
              "category": "Materials",
              "aliases": {"home depot", "the homedepot", "the home depot", ...},
              "history_count": 151,
          },
          ...
        }
    """
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE

    index: dict[str, dict] = {}

    # 1. Seed vendors
    for category, names in SEED_VENDORS.items():
        for canon in names:
            key = canon
            if key not in index:
                index[key] = {
                    "canonical": canon,
                    "category": category,
                    "aliases": {_norm(canon)},
                    "history_count": 0,
                }
            else:
                index[key]["aliases"].add(_norm(canon))

    # 2. History from ahb_receipts — used to add aliases + boost category confidence.
    # Only promote a historical raw_name to a canonical alias when the fuzzy score
    # is strong (>= 0.85); otherwise it's probably OCR garbage that shouldn't
    # collapse into a seed vendor. Count still tracked for the closest match if
    # any, but garbage rows like "Act #" stay out of the alias set.
    history = _load_history_vendors()
    for raw_name, cat, count in history:
        best_canon, score = _fuzzy_best_canonical(raw_name, list(index.keys()))
        if best_canon and score >= 0.85:
            index[best_canon]["aliases"].add(_norm(raw_name))
            index[best_canon]["history_count"] += count

    # 3. Learned aliases from receipt_learn.json
    learned = _load_learned()
    for raw_alias, canon in (learned.get("vendor_aliases") or {}).items():
        if canon in index:
            index[canon]["aliases"].add(_norm(raw_alias))
    for canon, override_cat in (learned.get("vendor_category_overrides") or {}).items():
        if canon in index and override_cat:
            index[canon]["category"] = override_cat

    _INDEX_CACHE = index
    return index


_WORD_RE = re.compile(r"\b\w+\b")


def _token_set(s: str) -> set[str]:
    return {w for w in _WORD_RE.findall(s) if len(w) >= 3}


def _fuzzy_best_canonical(raw: str, canonicals: list[str]) -> tuple[str | None, float]:
    """Fuzzy-match raw against a list of canonical names. Returns (canon, ratio).
    Tries (in order): exact, space-collapsed exact (homedepot==home depot),
    whole-word containment for shorter side >= 5 chars (prevents act→tractor),
    token overlap, sequence ratio."""
    if not raw:
        return (None, 0.0)
    n = _norm(raw)
    nc = _collapsed(raw)
    n_tokens = _token_set(n)
    best = (None, 0.0)
    for c in canonicals:
        cn = _norm(c)
        cnc = _collapsed(c)
        if not cn:
            continue
        if n == cn:
            return (c, 1.0)

        # Space-insensitive exact match: "homedepot" matches "home depot"
        if nc == cnc and nc:
            return (c, 1.0)

        # Collapsed containment (e.g. "wawa123premium" contains "wawa")
        if cnc and (cnc in nc or nc in cnc):
            shorter_c, longer_c = (nc, cnc) if len(nc) <= len(cnc) else (cnc, nc)
            if len(shorter_c) >= 4 and shorter_c in longer_c:
                score = 0.93
                if score > best[1]:
                    best = (c, score)
                continue

        # Word-boundary containment on space-normalized form: shorter side
        # must appear as a whole phrase in longer side, and be >= 5 chars.
        shorter, longer = (n, cn) if len(n) <= len(cn) else (cn, n)
        if len(shorter) >= 5:
            pat = r"\b" + re.escape(shorter) + r"\b"
            if re.search(pat, longer):
                score = 0.92
                if score > best[1]:
                    best = (c, score)
                continue

        # Token overlap (multi-char shared tokens).
        cn_tokens = _token_set(cn)
        if n_tokens and cn_tokens:
            common = n_tokens & cn_tokens
            if common:
                overlap = len(common) / min(len(n_tokens), len(cn_tokens))
                if overlap >= 0.5:
                    tok_score = 0.85 + 0.10 * overlap
                    if tok_score > best[1]:
                        best = (c, tok_score)
                    continue

        # Fallback: sequence-match ratio.
        ratio = difflib.SequenceMatcher(None, n, cn).ratio()
        if ratio > best[1]:
            best = (c, ratio)
    return best


def match_vendor(raw: str) -> tuple[str, str, float]:
    """Return (canonical, category, confidence) for a raw vendor string.
    Confidence < 0.82 means "no strong match" — caller should keep the original."""
    if not raw or not raw.strip():
        return ("", "", 0.0)
    index = load_vendor_index()

    # Direct alias match (cheap, highest signal)
    raw_n = _norm(raw)
    for canon, meta in index.items():
        if raw_n in meta["aliases"]:
            return (canon, meta["category"], 1.0)

    # Fuzzy against canonicals
    best_canon, score = _fuzzy_best_canonical(raw, list(index.keys()))
    if best_canon and score >= 0.82:
        meta = index[best_canon]
        return (best_canon, meta["category"], score)

    # No strong match — return raw passed through, empty category, low confidence
    return (raw.strip(), "", score)


def suggest_category_from_items(items: list) -> str:
    """Fallback: look at item names for food/materials hints. Returns '' if unsure."""
    if not items or not isinstance(items, list):
        return ""
    joined = " ".join(
        str(i.get("name", "")) if isinstance(i, dict) else str(i) for i in items
    ).lower()
    if any(h in joined for h in FOOD_ITEM_HINTS):
        return "Food"
    return ""


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        # Print the index as JSON for inspection
        idx = load_vendor_index()
        out = {
            k: {
                **v,
                "aliases": sorted(v["aliases"]),
            }
            for k, v in idx.items()
        }
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        raw = " ".join(sys.argv[1:])
        canon, cat, conf = match_vendor(raw)
        print(json.dumps({"input": raw, "canonical": canon, "category": cat, "confidence": conf}, indent=2))
