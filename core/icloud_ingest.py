#!/usr/bin/env python3
"""
Baza iCloud Ingest Engine
─────────────────────────
Multi-tenant iCloud → Baza migration pipeline.

For each registered iCloud account:
  1. Run icloudpd (incremental, EXIF-preserving) to download new photos & videos
  2. Walk new files, extract EXIF (date, GPS)
  3. Classify each file:
       - "ahb_jobsite" → register in ahb_photos (work bin) AND copy to user's cloud
       - "personal"    → leave in user's Baza Cloud personal media folder
  4. Update sync state per account
  5. Return a summary the caller can show / send to Telegram

Designed to run in 3 modes:
  - Single-tenant admin (Serge): user_id=None, dirs use ICLOUD_ADMIN_DIR
  - Cloud user:                  user_id=<id>, dirs under CLOUD_STORAGE/<id>/icloud/
  - Cron / API trigger:          either of the above

Apple ID credentials:
  - For single-tenant: env vars ICLOUD_APPLE_ID + ICLOUD_APP_PASSWORD (app-specific pwd)
  - For cloud users:   stored in cloud_icloud_accounts table (per-user, encrypted-at-rest
                       optional; for now stored as plain text in PG since the user is
                       opting in by giving us the credential and the DB is not exposed
                       outside the box)

Cookie / 2FA session storage: per-account directory under COOKIE_ROOT — once a user has
authenticated once via /scripts/icloud_setup.py the cookie is reused indefinitely.
"""
from __future__ import annotations
import os, sys, json, sqlite3, datetime, subprocess, shutil, re, logging
from pathlib import Path

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

VENV_PYTHON = os.path.join(FRAMEWORK_DIR, "venv", "bin", "python")
ICLOUDPD    = os.path.join(FRAMEWORK_DIR, "venv", "bin", "icloudpd")

DASHBOARD_DB    = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
PHOTOS_DIR      = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts", "ahb123-photos")
CLOUD_STORAGE   = "/mnt/empirepool/cloud"
ICLOUD_ADMIN_DIR= "/mnt/empirepool/media/icloud"      # single-tenant Serge dump
COOKIE_ROOT     = os.path.expanduser("~/.baza-icloud-cookies")
LOG_DIR         = os.path.join(FRAMEWORK_DIR, "logs")

os.makedirs(PHOTOS_DIR, exist_ok=True)
os.makedirs(ICLOUD_ADMIN_DIR, exist_ok=True)
os.makedirs(COOKIE_ROOT, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

log = logging.getLogger("icloud_ingest")
if not log.handlers:
    h = logging.FileHandler(os.path.join(LOG_DIR, "icloud_ingest.log"))
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

# Image extensions we treat as photos for the AHB classifier (videos still get
# downloaded by icloudpd but live in personal storage by default).
IMG_EXTS  = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}
VIDEO_EXTS= {".mov", ".mp4", ".m4v", ".avi", ".mkv"}


# ─────────────────────────────────────────────────────────────────────────────
# Account storage (PostgreSQL — uses existing pool from core.context_db)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_table():
    """One-time PG table creation. Safe to call repeatedly."""
    try:
        from core.context_db import get_pool
        pool = get_pool()
        conn = pool.getconn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cloud_icloud_accounts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,                           -- NULL = single-tenant admin
                apple_id VARCHAR(255) NOT NULL,
                app_password TEXT,                          -- app-specific password
                cookie_dir TEXT NOT NULL,
                download_dir TEXT NOT NULL,
                personal_dir TEXT,                          -- where personal media is routed
                auto_classify BOOLEAN DEFAULT TRUE,
                ahb_owner BOOLEAN DEFAULT FALSE,            -- if TRUE, work photos go to AHB123
                last_sync TIMESTAMP,
                last_status TEXT,
                total_synced INTEGER DEFAULT 0,
                total_jobsite INTEGER DEFAULT 0,
                total_personal INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, apple_id)
            )
        """)
        conn.commit()
        cur.close()
        pool.putconn(conn)
    except Exception as e:
        log.error(f"PG table init failed: {e}")

_ensure_table()


def list_accounts(user_id=None, include_admin=False):
    """Return list of dict accounts for the given cloud user (or admin if user_id=None)."""
    from core.context_db import get_pool
    pool = get_pool()
    conn = pool.getconn()
    cur = conn.cursor()
    if user_id is None and include_admin:
        cur.execute("SELECT id,user_id,apple_id,cookie_dir,download_dir,personal_dir,auto_classify,ahb_owner,last_sync,last_status,total_synced,total_jobsite,total_personal FROM cloud_icloud_accounts WHERE user_id IS NULL ORDER BY id")
    elif user_id is None:
        cur.execute("SELECT id,user_id,apple_id,cookie_dir,download_dir,personal_dir,auto_classify,ahb_owner,last_sync,last_status,total_synced,total_jobsite,total_personal FROM cloud_icloud_accounts ORDER BY id")
    else:
        cur.execute("SELECT id,user_id,apple_id,cookie_dir,download_dir,personal_dir,auto_classify,ahb_owner,last_sync,last_status,total_synced,total_jobsite,total_personal FROM cloud_icloud_accounts WHERE user_id=%s ORDER BY id", (user_id,))
    rows = cur.fetchall()
    cur.close()
    pool.putconn(conn)
    keys = ["id","user_id","apple_id","cookie_dir","download_dir","personal_dir",
            "auto_classify","ahb_owner","last_sync","last_status","total_synced",
            "total_jobsite","total_personal"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        if d.get("last_sync"):
            d["last_sync"] = d["last_sync"].isoformat()
        out.append(d)
    return out


def add_account(apple_id: str, app_password: str, user_id=None, ahb_owner=False, auto_classify=True):
    """Create a new iCloud account binding. Returns the new account row."""
    apple_id = apple_id.strip().lower()
    if user_id is None:
        download_dir = os.path.join(ICLOUD_ADMIN_DIR, _safe(apple_id))
        personal_dir = os.path.join(ICLOUD_ADMIN_DIR, _safe(apple_id), "_personal")
    else:
        user_root = os.path.join(CLOUD_STORAGE, str(user_id))
        download_dir = os.path.join(user_root, "icloud", _safe(apple_id))
        personal_dir = os.path.join(user_root, "Photos from iCloud")
    cookie_dir = os.path.join(COOKIE_ROOT, _safe(apple_id) + (f"_u{user_id}" if user_id else ""))
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs(personal_dir, exist_ok=True)
    os.makedirs(cookie_dir, exist_ok=True)

    from core.context_db import get_pool
    pool = get_pool()
    conn = pool.getconn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO cloud_icloud_accounts
            (user_id, apple_id, app_password, cookie_dir, download_dir, personal_dir,
             auto_classify, ahb_owner)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (user_id, apple_id) DO UPDATE SET
            app_password=EXCLUDED.app_password,
            auto_classify=EXCLUDED.auto_classify,
            ahb_owner=EXCLUDED.ahb_owner
        RETURNING id
    """, (user_id, apple_id, app_password, cookie_dir, download_dir, personal_dir,
          auto_classify, ahb_owner))
    aid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    pool.putconn(conn)
    log.info(f"add_account user_id={user_id} apple_id={apple_id} → id={aid}")
    return aid


def remove_account(account_id: int, user_id=None):
    from core.context_db import get_pool
    pool = get_pool()
    conn = pool.getconn()
    cur = conn.cursor()
    if user_id is None:
        cur.execute("DELETE FROM cloud_icloud_accounts WHERE id=%s", (account_id,))
    else:
        cur.execute("DELETE FROM cloud_icloud_accounts WHERE id=%s AND user_id=%s",
                    (account_id, user_id))
    conn.commit()
    cur.close()
    pool.putconn(conn)


def _get_account(account_id: int):
    from core.context_db import get_pool
    pool = get_pool()
    conn = pool.getconn()
    cur = conn.cursor()
    cur.execute("""SELECT id,user_id,apple_id,app_password,cookie_dir,download_dir,
                          personal_dir,auto_classify,ahb_owner FROM cloud_icloud_accounts
                   WHERE id=%s""", (account_id,))
    row = cur.fetchone()
    cur.close()
    pool.putconn(conn)
    if not row:
        return None
    keys = ["id","user_id","apple_id","app_password","cookie_dir","download_dir",
            "personal_dir","auto_classify","ahb_owner"]
    return dict(zip(keys, row))


def _update_state(account_id: int, *, status=None, synced=0, jobsite=0, personal=0):
    from core.context_db import get_pool
    pool = get_pool()
    conn = pool.getconn()
    cur = conn.cursor()
    cur.execute("""UPDATE cloud_icloud_accounts SET
                     last_sync=NOW(),
                     last_status=COALESCE(%s, last_status),
                     total_synced  = total_synced  + %s,
                     total_jobsite = total_jobsite + %s,
                     total_personal= total_personal+ %s
                   WHERE id=%s""",
                (status, synced, jobsite, personal, account_id))
    conn.commit()
    cur.close()
    pool.putconn(conn)


def _safe(s: str) -> str:
    return re.sub(r"[^\w.-]", "_", s)


# ─────────────────────────────────────────────────────────────────────────────
# icloudpd wrapper
# ─────────────────────────────────────────────────────────────────────────────

def run_icloudpd(account: dict, recent: int = None, until_found: int = 100, dry_run: bool = False) -> dict:
    """Run icloudpd for one account. Returns dict with status, downloaded count, log tail."""
    apple_id     = account["apple_id"]
    cookie_dir   = account["cookie_dir"]
    download_dir = account["download_dir"]
    app_password = account.get("app_password") or ""

    cmd = [
        ICLOUDPD,
        "--directory",         download_dir,
        "--cookie-directory",  cookie_dir,
        "--username",          apple_id,
        "--folder-structure",  "{:%Y/%m}",
        "--set-exif-datetime",
        "--no-progress-bar",
        "--log-level",         "info",
        "--password-provider", "parameter",
        "-p",                  app_password,
    ]
    if recent:
        cmd += ["--recent", str(recent)]
    elif until_found:
        cmd += ["--until-found", str(until_found)]
    if dry_run:
        cmd += ["--only-print-filenames"]

    log.info(f"icloudpd starting for {apple_id} → {download_dir}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        # Count downloaded files heuristically from log lines
        downloaded = len(re.findall(r"Downloaded ", out))
        # Check for auth/session/connection errors in the log even if exit code is 0
        low = out.lower()
        if "invalid email/password" in low or "-20101" in out or "check the account information" in low:
            return {"ok": False, "downloaded": 0, "log": out[-1500:],
                    "error": "Apple rejected the credentials. You must use an APP-SPECIFIC PASSWORD generated at account.apple.com → Sign-In and Security → App-Specific Passwords. Your regular Apple ID password will not work."}
        if "cannot connect to apple icloud service" in low or "connection refused" in low or "connection error" in low:
            return {"ok": False, "downloaded": 0, "log": out[-1500:],
                    "error": "Cannot connect to Apple iCloud — likely rate-limited after a failed login. Wait 15-30 minutes and try again with a valid app-specific password."}
        if "2fa" in low or "mfa" in low or "session has expired" in low or "two-step" in low or "authentication required" in low:
            return {"ok": False, "needs_2fa": True, "downloaded": 0,
                    "log": out[-1500:], "error": "Authentication required — re-run setup to enter 2FA code"}
        if proc.returncode != 0:
            return {"ok": False, "downloaded": downloaded, "log": out[-1500:],
                    "error": f"icloudpd exit {proc.returncode}"}
        return {"ok": True, "downloaded": downloaded, "log": out[-1500:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "downloaded": 0, "log": "", "error": "icloudpd timed out (1h)"}
    except Exception as e:
        return {"ok": False, "downloaded": 0, "log": "", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# EXIF + classifier
# ─────────────────────────────────────────────────────────────────────────────

def extract_exif(filepath: str) -> dict:
    """Pull date/time/gps from EXIF; gracefully handles HEIC if pillow-heif is present."""
    try:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS
        img = Image.open(filepath)
        exif = img._getexif() or {}
        out = {}
        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "DateTimeOriginal" and isinstance(value, str):
                try:
                    dt = datetime.datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                    out["photo_date"] = dt.strftime("%Y-%m-%d")
                    out["photo_time"] = dt.strftime("%H:%M")
                except Exception:
                    pass
            if tag == "GPSInfo":
                gps = {GPSTAGS.get(k, k): v for k, v in value.items()}
                def to_deg(val, ref):
                    try:
                        d, m, s = val
                        deg = float(d) + float(m)/60 + float(s)/3600
                        if ref in ("S", "W"):
                            deg = -deg
                        return round(deg, 6)
                    except Exception:
                        return None
                if "GPSLatitude" in gps and "GPSLatitudeRef" in gps:
                    out["latitude"]  = to_deg(gps["GPSLatitude"], gps["GPSLatitudeRef"])
                if "GPSLongitude" in gps and "GPSLongitudeRef" in gps:
                    out["longitude"] = to_deg(gps["GPSLongitude"], gps["GPSLongitudeRef"])
        return out
    except Exception:
        return {}


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    import math
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp     = math.radians(lat2 - lat1)
    dl     = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))


def load_project_locations() -> list:
    """Pull AHB projects with lat/lon (or address that geocodes) for the classifier."""
    if not os.path.exists(DASHBOARD_DB):
        return []
    try:
        conn = sqlite3.connect(DASHBOARD_DB)
        conn.row_factory = sqlite3.Row
        # Try multiple plausible column names; ahb_projects schema varies
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ahb_projects)").fetchall()]
        sel = ["id"]
        for c in ("name","title","project_name","client_name","address","location","lat","lng","latitude","longitude","start_date","end_date","status"):
            if c in cols:
                sel.append(c)
        rows = conn.execute(f"SELECT {','.join(sel)} FROM ahb_projects").fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            lat = d.get("latitude") or d.get("lat")
            lng = d.get("longitude") or d.get("lng")
            try: lat = float(lat) if lat is not None else None
            except: lat = None
            try: lng = float(lng) if lng is not None else None
            except: lng = None
            d["_lat"] = lat
            d["_lng"] = lng
            d["_name"] = d.get("name") or d.get("title") or d.get("project_name") or d.get("client_name") or f"project-{d['id']}"
            out.append(d)
        return out
    except Exception as e:
        log.warning(f"load_project_locations: {e}")
        return []


def classify_photo(exif: dict, projects: list, ahb_owner: bool) -> dict:
    """Decide whether a photo is jobsite vs personal.
    Heuristics (only one needs to match):
      1. GPS coords within 100m of any AHB project location
      2. Photo date falls inside a project's start/end window AND user is ahb_owner
      3. Filename contains job/site keywords (handled by caller via filename param if desired)
    Returns: {"phase": None, "project_name": str|None, "category": str|None, "reason": str}
    """
    if not ahb_owner:
        return {"is_jobsite": False, "reason": "user is not AHB owner"}

    lat = exif.get("latitude")
    lng = exif.get("longitude")
    pdate = exif.get("photo_date")

    # 1. GPS proximity
    if lat is not None and lng is not None:
        for p in projects:
            if p["_lat"] and p["_lng"]:
                d = _haversine_km(lat, lng, p["_lat"], p["_lng"])
                if d <= 0.1:  # 100 meters
                    return {"is_jobsite": True, "project_name": p["_name"],
                            "reason": f"GPS within {int(d*1000)}m of {p['_name']}"}

    # 2. Date window match (only useful if there's also a project location matched separately)
    if pdate:
        for p in projects:
            sd, ed = p.get("start_date"), p.get("end_date")
            if sd and ed and sd <= pdate <= ed:
                return {"is_jobsite": True, "project_name": p["_name"],
                        "reason": f"photo date {pdate} inside project window"}

    return {"is_jobsite": False, "reason": "no project match"}


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline: sync → classify → import
# ─────────────────────────────────────────────────────────────────────────────

def _list_files_recursive(root: str) -> list:
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            out.append(os.path.join(dirpath, f))
    return out


def _seen_set(account_id: int) -> set:
    """Track which file paths we've already classified for this account."""
    state_file = os.path.join(LOG_DIR, f"icloud_seen_{account_id}.txt")
    if not os.path.exists(state_file):
        return set()
    with open(state_file) as f:
        return set(l.strip() for l in f if l.strip())


def _save_seen(account_id: int, paths: set):
    state_file = os.path.join(LOG_DIR, f"icloud_seen_{account_id}.txt")
    with open(state_file, "w") as f:
        for p in sorted(paths):
            f.write(p + "\n")


def _curate_imported_photo(filepath: str, project_name: str = None) -> bool:
    """Run Phil's curator on a freshly-imported jobsite photo so it gets project +
    work-phase metadata in the document library."""
    try:
        skill = os.path.join(FRAMEWORK_DIR, "skills", "shared", "curate_document.py")
        if not os.path.exists(skill):
            return False
        env = os.environ.copy()
        env["SKILL_ARGS"] = json.dumps({
            "file_path": filepath,
            "agent_id": "phil_hass",
        })
        proc = subprocess.run(
            [VENV_PYTHON, skill],
            env=env,
            capture_output=True, text=True, timeout=180
        )
        if proc.returncode != 0:
            log.warning(f"curate {filepath} exit {proc.returncode}: {(proc.stderr or '')[:200]}")
            return False
        # If we know the project, also persist project_id on the doc row
        if project_name:
            try:
                conn = sqlite3.connect(DASHBOARD_DB)
                conn.execute("UPDATE ahb_documents SET project_id = ? WHERE file_path = ?",
                             (project_name, filepath))
                conn.commit()
                conn.close()
            except Exception:
                pass
        return True
    except Exception as e:
        log.warning(f"_curate_imported_photo failed: {e}")
        return False


def import_to_ahb_photos(filepath: str, exif: dict, project_name: str = None):
    """Copy a jobsite photo into the AHB photos pool and register it in SQLite.
    Returns the destination path on success (so the caller can curate it), None on failure."""
    try:
        ts = int(datetime.datetime.now().timestamp() * 1000)
        ext = os.path.splitext(filepath)[1].lower()
        safe = _safe(os.path.basename(filepath))
        fname = f"icloud_{ts}_{safe}"
        dest  = os.path.join(PHOTOS_DIR, fname)
        shutil.copy2(filepath, dest)
        size = os.path.getsize(dest)
        conn = sqlite3.connect(DASHBOARD_DB)
        conn.execute("""CREATE TABLE IF NOT EXISTS ahb_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL, project_name TEXT, client_name TEXT,
            location TEXT, phase TEXT, category TEXT,
            photo_date TEXT, photo_time TEXT,
            latitude REAL, longitude REAL,
            notes TEXT, size INTEGER,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""INSERT INTO ahb_photos
            (filename, project_name, location, photo_date, photo_time,
             latitude, longitude, notes, size)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (fname, project_name or "iCloud Import", None,
             exif.get("photo_date"), exif.get("photo_time"),
             exif.get("latitude"), exif.get("longitude"),
             "Auto-imported from iCloud", size))
        conn.commit()
        conn.close()
        return dest
    except Exception as e:
        log.error(f"import_to_ahb_photos {filepath}: {e}")
        return None


def route_to_personal(filepath: str, account: dict) -> bool:
    """Move/copy a personal media file into the user's Baza Cloud personal folder.
    The file is already in the user's per-account download_dir (which itself lives under
    user_root), so for cloud users we just leave it there. For admin mode we copy
    a hardlink/symlink into the personal subdir for organisation.
    """
    try:
        if account["user_id"] is None:
            # Admin mode — symlink into _personal/{YYYY-MM}/
            dt = None
            ex = extract_exif(filepath)
            if ex.get("photo_date"):
                dt = ex["photo_date"][:7]
            else:
                dt = datetime.datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m")
            target_dir = os.path.join(account["personal_dir"], dt)
            os.makedirs(target_dir, exist_ok=True)
            link = os.path.join(target_dir, os.path.basename(filepath))
            if not os.path.exists(link):
                try: os.symlink(filepath, link)
                except OSError: shutil.copy2(filepath, link)
        # Cloud-user mode: file already lives at user_root/icloud/<apple>/YYYY/MM/...
        # which is visible in their cloud — nothing else to do.
        return True
    except Exception as e:
        log.warning(f"route_to_personal {filepath}: {e}")
        return False


def ingest_account(account_id: int, recent: int = None, until_found: int = 100) -> dict:
    """Top-level: sync one account, classify new files, route them. Returns summary."""
    account = _get_account(account_id)
    if not account:
        return {"ok": False, "error": f"account {account_id} not found"}

    log.info(f"ingest_account {account_id} ({account['apple_id']})")

    # 1. Run icloudpd
    sync = run_icloudpd(account, recent=recent, until_found=until_found)
    if not sync["ok"]:
        _update_state(account_id, status=sync.get("error","failed"))
        return {"ok": False, "account": account["apple_id"], **sync}

    # 2. Walk download dir for new files
    all_files = _list_files_recursive(account["download_dir"])
    seen = _seen_set(account_id)
    new_files = [f for f in all_files if f not in seen]

    log.info(f"found {len(new_files)} new files (total: {len(all_files)})")

    # 3. Load AHB project locations once
    projects = load_project_locations() if account["auto_classify"] else []

    # 4. Classify + route each new file
    jobsite_count = 0
    personal_count = 0
    curated_count = 0
    sample_jobsites = []
    for fp in new_files:
        ext = os.path.splitext(fp)[1].lower()
        if ext in IMG_EXTS:
            exif = extract_exif(fp)
            cls = classify_photo(exif, projects, account["ahb_owner"]) if account["auto_classify"] else {"is_jobsite": False}
            if cls["is_jobsite"]:
                copied = import_to_ahb_photos(fp, exif, cls.get("project_name"))
                if copied:
                    jobsite_count += 1
                    if len(sample_jobsites) < 5:
                        sample_jobsites.append(os.path.basename(fp) + " — " + cls.get("reason",""))
                    # Auto-curate the imported photo so Phil tags it with project + work-phase metadata
                    try:
                        if _curate_imported_photo(copied, project_name=cls.get("project_name")):
                            curated_count += 1
                    except Exception as e:
                        log.warning(f"curate failed for {copied}: {e}")
                    # Also keep the original accessible from personal folder
                    route_to_personal(fp, account)
                else:
                    route_to_personal(fp, account)
                    personal_count += 1
            else:
                route_to_personal(fp, account)
                personal_count += 1
        else:
            # Videos / other media → personal only
            route_to_personal(fp, account)
            personal_count += 1
        seen.add(fp)

    _save_seen(account_id, seen)
    _update_state(account_id, status="ok",
                  synced=len(new_files), jobsite=jobsite_count, personal=personal_count)

    return {
        "ok": True,
        "account": account["apple_id"],
        "user_id": account["user_id"],
        "downloaded": sync.get("downloaded", 0),
        "new_files": len(new_files),
        "jobsite":   jobsite_count,
        "personal":  personal_count,
        "curated":   curated_count,
        "samples":   sample_jobsites,
    }


def ingest_all(user_id=None) -> list:
    """Sync every account for a given user (or every admin account if user_id=None).
    Returns a list of per-account summaries.
    """
    accounts = list_accounts(user_id=user_id, include_admin=(user_id is None))
    results = []
    for acc in accounts:
        try:
            results.append(ingest_account(acc["id"]))
        except Exception as e:
            log.error(f"ingest_account {acc['id']} crashed: {e}")
            results.append({"ok": False, "account": acc["apple_id"], "error": str(e)})
    return results


# ─────────────────────────────────────────────────────────────────────────────
# CLI for manual / cron use
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Baza iCloud ingest")
    sub = p.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Register an iCloud account")
    p_add.add_argument("--apple-id", required=True)
    p_add.add_argument("--password", required=True, help="App-specific password")
    p_add.add_argument("--user-id",  type=int, default=None)
    p_add.add_argument("--ahb-owner", action="store_true")

    p_list = sub.add_parser("list")
    p_list.add_argument("--user-id", type=int, default=None)

    p_sync = sub.add_parser("sync")
    p_sync.add_argument("--id", type=int, help="account id")
    p_sync.add_argument("--user-id", type=int, default=None)
    p_sync.add_argument("--recent", type=int, default=None)
    p_sync.add_argument("--until-found", type=int, default=100)

    p_rm = sub.add_parser("remove")
    p_rm.add_argument("--id", type=int, required=True)

    args = p.parse_args()

    if args.cmd == "add":
        aid = add_account(args.apple_id, args.password, user_id=args.user_id,
                          ahb_owner=args.ahb_owner)
        print(json.dumps({"id": aid}, indent=2))
    elif args.cmd == "list":
        print(json.dumps(list_accounts(user_id=args.user_id, include_admin=(args.user_id is None)), indent=2, default=str))
    elif args.cmd == "sync":
        if args.id:
            print(json.dumps(ingest_account(args.id, recent=args.recent, until_found=args.until_found), indent=2))
        else:
            print(json.dumps(ingest_all(user_id=args.user_id), indent=2))
    elif args.cmd == "remove":
        remove_account(args.id)
        print("removed")
    else:
        p.print_help()
