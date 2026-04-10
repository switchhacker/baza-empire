#!/usr/bin/env python3
"""Phil's daily document watchdog.

Three jobs in one cron:
  1. Package follow-ups — for any package in 'submitted' status, ping Serge at
     30/60/90 days post-submission asking for a permit number / approval status.
  2. Doc expiration alerts — scan ahb_documents for COIs / licenses / W9s with
     extracted dates older than 11 months; warn 30 days before expiry.
  3. Vendor extraction — for any new COI / W9 since last run, parse the issuing
     entity into the ahb_vendors mini-CRM (idempotent on vendor name).

Runs daily at 7am via crontab.
"""
import os, sys, json, sqlite3, datetime, re, urllib.request, urllib.parse, logging

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
from agents.cron_helpers import send_telegram, save_artifact, log

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PHIL-WATCHDOG] %(message)s")

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _now():
    return datetime.datetime.now()


def _days_since(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso_str[:19].replace('Z',''))
        return (_now() - dt).days
    except Exception:
        return None


# ── Job 1: Package follow-ups ────────────────────────────────────────────────

def check_pending_packages():
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM ahb_app_packages WHERE status='submitted' AND submitted_at IS NOT NULL"
    ).fetchall()
    alerts = []
    for r in rows:
        days = _days_since(r['submitted_at'])
        if days is None:
            continue
        # Ping at 30, 60, 90 days
        last = r['last_reminder_at']
        last_days = _days_since(last) if last else 999
        should_ping = False
        if days >= 30 and last_days >= 30:
            should_ping = True
        if not should_ping:
            continue
        if r['permit_number']:
            continue  # already has approval data
        alerts.append({
            'id': r['id'],
            'name': r['name'],
            'type': r['package_type'],
            'days': days,
            'project_id': r['project_id'],
        })
        conn.execute("UPDATE ahb_app_packages SET last_reminder_at=? WHERE id=?",
                     (_now().isoformat(), r['id']))
    conn.commit()
    conn.close()
    return alerts


# ── Job 2: Doc expiration alerts ─────────────────────────────────────────────

# COIs and licenses typically expire 1 year from issue date. We use a heuristic:
# extract any explicit expiry from the summary first, fall back to "issue + 12 months".
EXPIRY_PATTERN = re.compile(
    r"(?:expir(?:es?|y|ation))[^.]*?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _parse_date(s):
    s = s.strip().replace('/', '-')
    for fmt in ("%Y-%m-%d", "%m-%d-%Y", "%m-%d-%y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def check_expiring_docs():
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM ahb_documents WHERE doc_type IN ('coi','license','w9','tax_document','permit')"
    ).fetchall()
    warnings = []
    for r in rows:
        # 1) explicit expires_at column wins
        expiry = None
        if r['expires_at']:
            expiry = _parse_date(r['expires_at'])
        # 2) regex on summary
        if not expiry and r['summary']:
            m = EXPIRY_PATTERN.search(r['summary'])
            if m:
                expiry = _parse_date(m.group(1))
        # 3) regex on full content
        if not expiry and r['content_text']:
            m = EXPIRY_PATTERN.search(r['content_text'][:4000])
            if m:
                expiry = _parse_date(m.group(1))
        # 4) fallback: COI/license = doc_date + 365 days
        if not expiry and r['doc_date'] and r['doc_type'] in ('coi','license'):
            issued = _parse_date(r['doc_date'])
            if issued:
                expiry = issued + datetime.timedelta(days=365)
        if not expiry:
            continue
        days_left = (expiry - _now()).days
        # Persist computed expiry
        if not r['expires_at']:
            try:
                conn.execute("UPDATE ahb_documents SET expires_at=? WHERE id=?",
                             (expiry.strftime("%Y-%m-%d"), r['id']))
            except Exception:
                pass
        # Warn 30 days before expiry, again at 7 days, again at 0 (expired)
        if days_left <= 30 and not r['expiry_alerted']:
            warnings.append({
                'id': r['id'],
                'doc_type': r['doc_type'],
                'entity': r['entity'],
                'expires': expiry.strftime("%Y-%m-%d"),
                'days_left': days_left,
            })
            try:
                conn.execute("UPDATE ahb_documents SET expiry_alerted=1 WHERE id=?", (r['id'],))
            except Exception:
                pass
    conn.commit()
    conn.close()
    return warnings


# ── Job 3: Vendor extraction from COI/W9 ─────────────────────────────────────

def upsert_vendor_from_doc(doc_row):
    """Pull issuer name + contact from a COI/W9 doc row and upsert into ahb_vendors."""
    entity = (doc_row['entity'] or '').strip()
    if not entity:
        return False
    text = (doc_row['content_text'] or '')[:6000]
    # Heuristic phone / email extraction
    phone = ''
    email = ''
    pm = re.search(r"\(?(\d{3})\)?[\s\-.](\d{3})[\s\-.](\d{4})", text)
    if pm: phone = f"({pm.group(1)}) {pm.group(2)}-{pm.group(3)}"
    em = re.search(r"[\w._%+\-]+@[\w.\-]+\.[A-Za-z]{2,}", text)
    if em: email = em.group(0)
    # EIN looks like ##-#######
    ein = ''
    en = re.search(r"\b(\d{2}-\d{7})\b", text)
    if en: ein = en.group(1)
    address = ''
    am = re.search(r"\d+\s+[A-Za-z][A-Za-z0-9 .,'\-]{5,80}(?:Ave|St|Rd|Blvd|Lane|Dr|Pkwy|Way|Court|Ct|Pl)\.?", text)
    if am: address = am.group(0)

    conn = _conn()
    existing = conn.execute("SELECT id FROM ahb_vendors WHERE name=?", (entity,)).fetchone()
    vendor_type = doc_row['doc_type']  # coi → insurance, w9 → vendor, license → contractor
    type_map = {'coi':'insurance','w9':'vendor','license':'contractor'}
    if existing:
        # Update relevant doc id field
        col = {'coi':'coi_doc_id','w9':'w9_doc_id','license':'license_doc_id'}.get(doc_row['doc_type'])
        if col:
            conn.execute(f"UPDATE ahb_vendors SET {col}=?, updated_at=? WHERE id=?",
                         (doc_row['id'], _now().isoformat(), existing['id']))
        if phone:   conn.execute("UPDATE ahb_vendors SET phone=COALESCE(NULLIF(phone,''),?) WHERE id=?", (phone, existing['id']))
        if email:   conn.execute("UPDATE ahb_vendors SET email=COALESCE(NULLIF(email,''),?) WHERE id=?", (email, existing['id']))
        if ein:     conn.execute("UPDATE ahb_vendors SET ein_or_ssn=COALESCE(NULLIF(ein_or_ssn,''),?) WHERE id=?", (ein, existing['id']))
        if address: conn.execute("UPDATE ahb_vendors SET address=COALESCE(NULLIF(address,''),?) WHERE id=?", (address, existing['id']))
        new = False
    else:
        col_value = {'coi':doc_row['id'] if doc_row['doc_type']=='coi' else None,
                     'w9':doc_row['id'] if doc_row['doc_type']=='w9' else None,
                     'license':doc_row['id'] if doc_row['doc_type']=='license' else None}
        conn.execute("""INSERT INTO ahb_vendors
            (name, vendor_type, phone, email, address, ein_or_ssn,
             coi_doc_id, w9_doc_id, license_doc_id)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (entity, type_map.get(doc_row['doc_type'],'other'), phone, email, address, ein,
             col_value['coi'], col_value['w9'], col_value['license']))
        new = True
    conn.commit()
    conn.close()
    return new


def extract_vendors_from_new_docs():
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM ahb_documents WHERE doc_type IN ('coi','w9','license') ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()
    new_vendors = []
    for r in rows:
        try:
            if upsert_vendor_from_doc(dict(r)):
                new_vendors.append(r['entity'])
        except Exception as e:
            log.warning(f"vendor upsert failed for doc {r['id']}: {e}")
    return new_vendors


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    log.info("Phil watchdog starting")

    pkg_alerts = check_pending_packages()
    expiring   = check_expiring_docs()
    new_vendors = extract_vendors_from_new_docs()

    msg_lines = []

    if pkg_alerts:
        msg_lines.append("📋 <b>Package Follow-ups Needed</b>")
        for p in pkg_alerts:
            msg_lines.append(f"• {p['name']} — {p['days']} days since submission. Need permit number / status update.")
        msg_lines.append("")

    if expiring:
        msg_lines.append("⚠️ <b>Documents Expiring Soon</b>")
        for d in expiring:
            urgency = "⛔ EXPIRED" if d['days_left'] < 0 else f"{d['days_left']} days left"
            msg_lines.append(f"• {d['doc_type'].upper()} for {d['entity']} — expires {d['expires']} ({urgency})")
        msg_lines.append("")

    if new_vendors:
        msg_lines.append(f"🤝 <b>New Vendors Indexed</b>")
        for v in new_vendors[:10]:
            msg_lines.append(f"• {v}")
        if len(new_vendors) > 10:
            msg_lines.append(f"  ...and {len(new_vendors)-10} more")
        msg_lines.append("")

    if not msg_lines:
        log.info("nothing to report")
        return

    msg_lines.insert(0, "<b>Phil's Daily Doc Watchdog</b>\n")
    body = "\n".join(msg_lines)
    try:
        send_telegram(body)
    except Exception as e:
        log.warning(f"telegram failed: {e}")
    try:
        save_artifact("proj-baza-empire",
                      f"phil_watchdog_{_now().strftime('%Y-%m-%d')}.md",
                      body.replace("<b>","**").replace("</b>","**"))
    except Exception:
        pass
    log.info(f"done: pkg_alerts={len(pkg_alerts)} expiring={len(expiring)} new_vendors={len(new_vendors)}")


if __name__ == "__main__":
    main()
