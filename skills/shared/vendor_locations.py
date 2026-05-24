#!/usr/bin/env python3
"""
Baza Empire — Vendor Locations KB

Per-vendor address resolver. Backs the receipt-OCR address pipeline + the
ahb123 Vendors tab.

Three responsibilities:
  1. Maintain the `ahb_vendor_locations` table (schema + idempotent seed).
  2. Reject hallucinated placeholder addresses ("123 Main St / Anytown / USA")
     that local vision models drop in when they cannot read the printed text.
  3. Look up the canonical address for a vendor given raw OCR text — by
     street, ZIP, phone, or city match against known locations.

Public API:
    init_vendor_locations_table(conn)
    seed_vendor_locations(conn, seed_path=None, force=False) -> {"inserted", "skipped"}
    is_placeholder_address(text) -> bool
    lookup_location(canonical, raw_text, locations=None) -> dict | None
    all_locations(canonical, conn=None) -> list[dict]
    vendor_is_online(canonical, conn=None) -> bool
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
FRAMEWORK_DIR = HERE.parent.parent
DASHBOARD_DB = FRAMEWORK_DIR / "dashboard" / "baza_projects.db"
DEFAULT_SEED = HERE / "vendor_locations_seed.json"


# ── Schema ────────────────────────────────────────────────────────────────────

def init_vendor_locations_table(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE — safe to call on every dashboard startup."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ahb_vendor_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_canonical TEXT NOT NULL,
            vendor_type TEXT,
            is_online INTEGER NOT NULL DEFAULT 0,
            store_number TEXT,
            name TEXT,
            street TEXT,
            city TEXT,
            state TEXT,
            zip TEXT,
            phone TEXT,
            notes TEXT,
            source TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(vendor_canonical, street, city, state, zip)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vloc_canonical ON ahb_vendor_locations(vendor_canonical)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vloc_zip ON ahb_vendor_locations(zip)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vloc_phone ON ahb_vendor_locations(phone)")
    conn.commit()


def seed_vendor_locations(conn: sqlite3.Connection,
                          seed_path: str | Path | None = None,
                          force: bool = False) -> dict:
    """Load seed JSON into the table. Idempotent (uses UNIQUE constraint).

    force=True will UPDATE matching rows; False (default) keeps existing
    user-edited values intact and only inserts what's missing.
    """
    seed_path = Path(seed_path) if seed_path else DEFAULT_SEED
    if not seed_path.exists():
        return {"inserted": 0, "skipped": 0, "error": f"seed not found: {seed_path}"}

    data = json.loads(seed_path.read_text())
    vendors = data.get("vendors") or []
    inserted = 0
    skipped = 0

    for v in vendors:
        canon = (v.get("canonical") or "").strip()
        if not canon:
            continue
        vtype = v.get("vendor_type") or ""
        is_online = 1 if v.get("is_online") else 0
        locations = v.get("locations") or []

        # Online vendors get one synthetic row so the table can answer
        # "is Amazon online?" without a parallel structure.
        if is_online and not locations:
            try:
                conn.execute("""
                    INSERT INTO ahb_vendor_locations
                        (vendor_canonical, vendor_type, is_online,
                         store_number, name, street, city, state, zip, phone, notes, source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (canon, vtype, 1, None, "Online", None, None, None, None, None,
                      "Online-only vendor (no physical location).", "seed"))
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1
            continue

        for loc in locations:
            street = loc.get("street")
            city = loc.get("city")
            state = loc.get("state")
            zipc = loc.get("zip")
            try:
                if force:
                    conn.execute("""
                        INSERT INTO ahb_vendor_locations
                            (vendor_canonical, vendor_type, is_online,
                             store_number, name, street, city, state, zip, phone, notes, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(vendor_canonical, street, city, state, zip) DO UPDATE SET
                            vendor_type=excluded.vendor_type,
                            is_online=excluded.is_online,
                            store_number=excluded.store_number,
                            name=excluded.name,
                            phone=excluded.phone,
                            notes=excluded.notes,
                            source=excluded.source,
                            updated_at=CURRENT_TIMESTAMP
                    """, (canon, vtype, is_online,
                          loc.get("store_number"), loc.get("name"),
                          street, city, state, zipc,
                          loc.get("phone"), loc.get("notes"), loc.get("source") or "seed"))
                    inserted += 1
                else:
                    conn.execute("""
                        INSERT OR IGNORE INTO ahb_vendor_locations
                            (vendor_canonical, vendor_type, is_online,
                             store_number, name, street, city, state, zip, phone, notes, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (canon, vtype, is_online,
                          loc.get("store_number"), loc.get("name"),
                          street, city, state, zipc,
                          loc.get("phone"), loc.get("notes"), loc.get("source") or "seed"))
                    # Detect whether the INSERT actually happened
                    if conn.total_changes and conn.execute(
                        "SELECT changes()"
                    ).fetchone()[0]:
                        inserted += 1
                    else:
                        skipped += 1
            except sqlite3.IntegrityError:
                skipped += 1

    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


# ── Placeholder detection ────────────────────────────────────────────────────

# Vision models love to drop in fake/template addresses when the printed
# address is illegible. Patterns observed in production:
#   "123 Main St, Anytown, USA"     "123 Main Street Anytown US"
#   "1234 Main St City State ZIP"   "Your City, ST 00000"
#   "Address Line 1"                "Sample St, Sampletown, NY"
_PLACEHOLDER_PATTERNS = [
    re.compile(r"\b123\s+main\s+st(reet)?\b", re.I),
    re.compile(r"\b1234\s+main\s+st(reet)?\b", re.I),
    re.compile(r"\banytown\b", re.I),
    re.compile(r"\byour\s+city\b", re.I),
    re.compile(r"\bsample\s*town\b", re.I),
    re.compile(r"\baddress\s+line\s+1\b", re.I),
    re.compile(r"\b00000\b"),
    re.compile(r"\bcity\s*,\s*state\b", re.I),
    re.compile(r"\bstate\s+zip\b", re.I),
]


def is_placeholder_address(text: str | None) -> bool:
    """True if `text` looks like a hallucinated placeholder rather than a real
    printed address. Conservative — only flags well-known stand-ins."""
    if not text:
        return False
    s = str(text).strip()
    if not s:
        return False
    # Trim "USA" / "US" suffix when scoring — many real addresses end with it.
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(s):
            return True
    return False


# ── Address resolver ─────────────────────────────────────────────────────────

_STREET_NUM_RX = re.compile(r"\b(\d{2,5}[A-Z]?)\b")
_ZIP_RX = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_PHONE_RX = re.compile(r"(?<!\d)(\d{3})[\s\-.)]*(\d{3})[\s\-.]*(\d{4})(?!\d)")


def _norm_digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _norm_street(s: str | None) -> str:
    """Lowercase, collapse spaces, strip suite/unit, normalize common
    abbreviations so OCR'd 'CASTOR AVE.' matches seeded 'Castor Ave'."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\b(ste|suite|unit|apt|#)\s*\w+\b", "", s)
    s = re.sub(r"\b(avenue)\b", "ave", s)
    s = re.sub(r"\b(street)\b", "st", s)
    s = re.sub(r"\b(boulevard)\b", "blvd", s)
    s = re.sub(r"\b(road)\b", "rd", s)
    s = re.sub(r"\b(drive)\b", "dr", s)
    s = re.sub(r"\b(highway|hwy)\b", "hwy", s)
    s = re.sub(r"\b(turnpike|tpke)\b", "tpke", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_locations_for(canonical: str,
                        conn: sqlite3.Connection | None = None) -> list[dict]:
    own_conn = False
    if conn is None:
        if not DASHBOARD_DB.exists():
            return []
        conn = sqlite3.connect(str(DASHBOARD_DB))
        own_conn = True
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM ahb_vendor_locations WHERE vendor_canonical=? "
            "AND is_online=0 ORDER BY id",
            (canonical,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own_conn:
            conn.close()


def all_locations(canonical: str,
                  conn: sqlite3.Connection | None = None) -> list[dict]:
    """Public wrapper — returns every physical location for a vendor."""
    return _load_locations_for(canonical, conn)


def vendor_is_online(canonical: str,
                     conn: sqlite3.Connection | None = None) -> bool:
    own_conn = False
    if conn is None:
        if not DASHBOARD_DB.exists():
            return False
        conn = sqlite3.connect(str(DASHBOARD_DB))
        own_conn = True
    try:
        row = conn.execute(
            "SELECT is_online FROM ahb_vendor_locations "
            "WHERE vendor_canonical=? LIMIT 1",
            (canonical,),
        ).fetchone()
        if not row:
            return False
        return bool(row[0])
    finally:
        if own_conn:
            conn.close()


def format_location(loc: dict) -> str:
    """Render a location row as a single-line address string."""
    parts = []
    street = (loc.get("street") or "").strip()
    city = (loc.get("city") or "").strip()
    state = (loc.get("state") or "").strip()
    zipc = (loc.get("zip") or "").strip()
    if street:
        parts.append(street)
    csz_parts = []
    if city:
        csz_parts.append(city)
    if state:
        csz_parts.append(state)
    csz = " ".join([p for p in [", ".join(csz_parts), zipc] if p]).strip()
    if csz:
        parts.append(csz)
    return ", ".join(parts) if parts else ""


def lookup_location(canonical: str,
                    raw_text: str | None,
                    locations: Iterable[dict] | None = None,
                    conn: sqlite3.Connection | None = None,
                    min_score: int = 25) -> dict | None:
    """Find the best-matching known location for `canonical` given raw OCR text.

    Scoring (max 100):
      +50  store_number appears verbatim (per-store unique on the printed receipt)
      +40  phone digits match exactly (high signal — phone is per-store)
      +25  ZIP matches
      +20  street number AND street word both appear in raw_text
      +10  street word alone appears
      +10  city name appears in raw_text
      +5   state code appears

    Ambiguity guard: if the top two candidates tie and the top score is at
    or below `min_score`, return None — the signal is too weak to disambiguate
    (e.g. two same-vendor stores in the same ZIP and no other clue).

    Args:
      min_score: minimum score required to return a match. Callers backfilling
        empty addresses should pass a higher value (40+) — we don't want to
        guess when there's no prior value to improve on.
    """
    if not canonical:
        return None
    if locations is None:
        locations = _load_locations_for(canonical, conn)
    locations = list(locations) or []
    if not locations:
        return None
    if not raw_text:
        return None

    hay = raw_text or ""
    hay_lower = hay.lower()
    hay_norm = _norm_street(hay)
    hay_digits_phones = {"".join(m) for m in _PHONE_RX.findall(hay)}
    hay_zips = set(_ZIP_RX.findall(hay))

    scored: list[tuple[int, dict]] = []
    for loc in locations:
        if loc.get("is_online"):
            continue
        score = 0

        # Store number is per-location-unique on the printed receipt
        # ("Store 703791", "#4103", "STORE: 1980"). High single signal but
        # only when CONTEXT (STORE / STR / # / NO) sits next to the digits —
        # bare 4-digit runs also appear as item SKUs, register IDs, etc.
        store_no = (loc.get("store_number") or "").strip()
        if store_no:
            ctx_rx = re.compile(
                rf"\b(?:store|str|stor|store\s*(?:no|number|#))[\s.:#]*{re.escape(store_no)}\b"
                rf"|#\s*{re.escape(store_no)}\b",
                re.I,
            )
            if ctx_rx.search(hay):
                score += 50

        phone_d = _norm_digits(loc.get("phone"))
        if phone_d and phone_d in hay_digits_phones:
            score += 40

        zipc = (loc.get("zip") or "").strip()
        if zipc and zipc in hay_zips:
            score += 25

        street = loc.get("street") or ""
        if street:
            st_norm = _norm_street(street)
            # First token after the number is the street name
            st_tokens = st_norm.split()
            num = st_tokens[0] if st_tokens else ""
            word = " ".join(st_tokens[1:3]) if len(st_tokens) > 1 else ""
            has_num = bool(num) and re.search(rf"\b{re.escape(num)}\b", hay_norm) is not None
            has_word = bool(word) and word in hay_norm
            if has_num and has_word:
                score += 20
            elif has_word:
                score += 10

        city = (loc.get("city") or "").strip()
        if city:
            if re.search(rf"\b{re.escape(city.lower())}\b", hay.lower()):
                score += 10

        state = (loc.get("state") or "").strip()
        if state and len(state) == 2:
            if re.search(rf"\b{re.escape(state.upper())}\b", hay):
                score += 5

        if score > 0:
            scored.append((score, loc))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_loc = scored[0]
    if best_score < min_score:
        return None
    # Tie at a weak score = ambiguous (e.g. two stores in same ZIP, no
    # disambiguator). Refuse rather than guess. A strong score (40+, i.e.
    # phone match or street+number) is allowed to win even if tied.
    if len(scored) > 1 and scored[0][0] == scored[1][0] and best_score < 35:
        return None
    return best_loc


# ── CLI for ad-hoc seeding / testing ─────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="run seed_vendor_locations")
    ap.add_argument("--force", action="store_true", help="force UPDATE existing rows during seed")
    ap.add_argument("--lookup", nargs=2, metavar=("CANONICAL", "RAW"),
                    help="resolve a location from raw OCR text")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DASHBOARD_DB))
    init_vendor_locations_table(conn)

    if args.seed:
        result = seed_vendor_locations(conn, force=args.force)
        print(json.dumps(result, indent=2))
    if args.lookup:
        canon, raw = args.lookup
        result = lookup_location(canon, raw, conn=conn)
        print(json.dumps(result, indent=2, default=str))
    conn.close()
