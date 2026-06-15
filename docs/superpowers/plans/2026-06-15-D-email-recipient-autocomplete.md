# D — Email Recipient Autocomplete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the email composer suggest recipients as you type (like Gmail), sourced from email history **and** AHBCO clients.

**Architecture:** The backend `GET /api/email2/contacts/suggest` already returns history-based suggestions but only from the `emails` table. We extract its query into a testable helper `_contact_suggestions(con, q)` that also queries `ahb_clients` (both tables live in `baza_projects.db`, reachable from the same `_conn()`). On the frontend, the composer already has the `#cmpTo/#cmpCc/#cmpBcc` inputs and an empty `#acPop` popover — they're just not wired. We add a vanilla-JS typeahead that fetches the endpoint, renders into `#acPop`, and supports mouse + keyboard selection across multi-recipient fields.

**Tech Stack:** Python 3 / Flask blueprint (`dashboard/email_studio.py`), SQLite (`baza_projects.db`), pytest 9 (`tests/`), vanilla JS in a Jinja template (`dashboard/templates/email.html`).

**Reference — spec:** `docs/superpowers/specs/2026-06-15-ahb123-billing-crud-email-share-design.md` (Piece D).

**IMPORTANT after template edits:** `sudo systemctl restart baza-dashboard` (service runs `debug=False`, Jinja caches templates). The backend (`email_studio.py`) is picked up by the same restart.

---

## File Structure

- **Modify** `dashboard/email_studio.py` — add `_contact_suggestions(con, q, limit=12)` helper near `api_contact_suggest` (~line 1073); rewrite the route to call it.
- **Create** `tests/test_email_contacts.py` — unit tests for `_contact_suggestions` against a seeded temp DB.
- **Modify** `dashboard/templates/email.html` — add the autocomplete JS (functions + wiring) inside the existing `<script>` block after the compose functions (~line 987); the `#acPop` div (line ~539) and inputs (lines ~506–514) already exist.

---

## Task 1: Backend — `_contact_suggestions` helper (merge history + clients)

**Files:**
- Modify: `dashboard/email_studio.py` (add helper above `api_contact_suggest`, ~line 1072)
- Test: `tests/test_email_contacts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_email_contacts.py`:

```python
"""Tests for email_studio._contact_suggestions — recipient autocomplete source."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'dashboard'))
import email_studio  # noqa: E402


@pytest.fixture()
def con():
    """Temp DB seeded with the two tables _contact_suggestions reads."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE emails (from_addr TEXT);
        CREATE TABLE ahb_clients (id TEXT, name TEXT, email TEXT);
        """
    )
    # email history: two messages from the same vendor, one from a stranger
    c.executemany("INSERT INTO emails(from_addr) VALUES (?)", [
        ("Bob Vendor <bob@vendor.com>",),
        ("Bob Vendor <bob@vendor.com>",),
        ("Stranger <x@other.com>",),
    ])
    # clients: one whose email overlaps history, one history-only-miss
    c.executemany("INSERT INTO ahb_clients(id, name, email) VALUES (?,?,?)", [
        ("c1", "Bob Vendor", "bob@vendor.com"),     # dup email vs history
        ("c2", "Alice Client", "alice@client.com"),  # client-only
        ("c3", "No Email", ""),                      # must be skipped
    ])
    c.commit()
    return c


def test_short_query_returns_empty(con):
    assert email_studio._contact_suggestions(con, "b") == []


def test_client_matches_by_name(con):
    out = email_studio._contact_suggestions(con, "alice")
    assert [r["email"] for r in out] == ["alice@client.com"]
    assert out[0]["source"] == "client"


def test_dedup_email_prefers_client_over_history(con):
    out = email_studio._contact_suggestions(con, "vendor")
    emails = [r["email"] for r in out]
    assert emails.count("bob@vendor.com") == 1          # deduped
    assert out[0]["source"] == "client"                  # client wins ordering


def test_history_only_contact_included(con):
    out = email_studio._contact_suggestions(con, "other")
    assert [r["email"] for r in out] == ["x@other.com"]
    assert out[0]["source"] == "history"


def test_blank_client_email_skipped(con):
    out = email_studio._contact_suggestions(con, "no email")
    assert out == []


def test_missing_ahb_clients_table_degrades_gracefully():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE emails (from_addr TEXT)")
    c.execute("INSERT INTO emails(from_addr) VALUES ('Z <z@z.com>')")
    c.commit()
    out = email_studio._contact_suggestions(c, "z@z")
    assert [r["email"] for r in out] == ["z@z.com"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/baza-empire/agent-framework-v3 && ./venv/bin/python -m pytest tests/test_email_contacts.py -v`
Expected: FAIL — `AttributeError: module 'email_studio' has no attribute '_contact_suggestions'`

- [ ] **Step 3: Write minimal implementation**

In `dashboard/email_studio.py`, immediately **above** the `@email_bp.route("/api/email2/contacts/suggest" ...)` decorator (~line 1072), add:

```python
def _contact_suggestions(con, q, limit=12):
    """Recipient autocomplete: AHBCO clients first, then email history, deduped by email.

    `con` must have row_factory = sqlite3.Row. Reads `ahb_clients` and `emails`
    (both in baza_projects.db). Degrades gracefully if `ahb_clients` is absent.
    """
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    out, seen = [], set()

    # 1) Real clients first — these are the people you bill.
    try:
        crows = con.execute(
            """SELECT name, email FROM ahb_clients
               WHERE email IS NOT NULL AND TRIM(email) != ''
                 AND (LOWER(name) LIKE ? OR LOWER(email) LIKE ?)
               ORDER BY name LIMIT ?""",
            (f"%{q}%", f"%{q}%", limit),
        ).fetchall()
    except sqlite3.OperationalError:
        crows = []
    for r in crows:
        addr = (r["email"] or "").strip()
        key = addr.lower()
        if not addr or key in seen:
            continue
        seen.add(key)
        name = r["name"] or ""
        out.append({
            "name": name, "email": addr,
            "raw": f"{name} <{addr}>" if name else addr,
            "count": 0, "source": "client",
        })

    # 2) Email history (existing behaviour).
    hrows = con.execute(
        """SELECT from_addr, COUNT(*) AS n FROM emails
           WHERE LOWER(from_addr) LIKE ? GROUP BY from_addr ORDER BY n DESC LIMIT ?""",
        (f"%{q}%", limit),
    ).fetchall()
    for r in hrows:
        name, addr = parseaddr(r["from_addr"] or "")
        key = addr.lower()
        if not addr or key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name, "email": addr, "raw": r["from_addr"],
            "count": r["n"], "source": "history",
        })

    return out[:limit]
```

`parseaddr` and `sqlite3` are already imported at the top of `email_studio.py` (the existing route uses `parseaddr`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/baza-empire/agent-framework-v3 && ./venv/bin/python -m pytest tests/test_email_contacts.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
cd ~/baza-empire/agent-framework-v3
git add tests/test_email_contacts.py dashboard/email_studio.py
git commit -m "feat(email): contact suggestions merge ahb_clients + history (helper + tests)"
```

---

## Task 2: Backend — route uses the helper

**Files:**
- Modify: `dashboard/email_studio.py` — body of `api_contact_suggest` (~line 1073–1095)

- [ ] **Step 1: Replace the route body**

Replace the existing `api_contact_suggest` function body (everything after its `def` line, currently the inline `emails`-only query) with:

```python
@email_bp.route("/api/email2/contacts/suggest", methods=["GET"])
def api_contact_suggest():
    """Autocomplete contacts from AHBCO clients + email history."""
    q = request.args.get("q") or ""
    con = _conn()
    try:
        contacts = _contact_suggestions(con, q)
    finally:
        con.close()
    return jsonify({"contacts": contacts})
```

- [ ] **Step 2: Restart dashboard and verify the live endpoint**

```bash
sudo systemctl restart baza-dashboard
sleep 2
curl -s 'http://127.0.0.1:8888/api/email2/contacts/suggest?q=ser' | head -c 400; echo
```
Expected: JSON `{"contacts":[...]}` — a non-error response (results depend on live data; an empty list is acceptable if no client/history matches "ser").

- [ ] **Step 3: Run the full helper test again (regression)**

Run: `cd ~/baza-empire/agent-framework-v3 && ./venv/bin/python -m pytest tests/test_email_contacts.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd ~/baza-empire/agent-framework-v3
git add dashboard/email_studio.py
git commit -m "feat(email): contacts/suggest route uses merged helper"
```

---

## Task 3: Frontend — wire the typeahead into the composer

**Files:**
- Modify: `dashboard/templates/email.html` — add JS inside the existing `<script>` block, after the compose functions (~after line 987). The `#acPop` div (~539) and `#cmpTo/#cmpCc/#cmpBcc` inputs (~506–514) already exist.

No automated JS test harness exists in this repo, so this task is verified manually in the browser (Step 3).

- [ ] **Step 1: Add the autocomplete JS**

Insert this block inside the `<script>` in `email.html`, immediately after the `sendCompose` / compose helper functions (around line 987, before the closing of that script section):

```javascript
/* ── Recipient autocomplete (To/Cc/Bcc) ───────────────────────────── */
(function(){
  const pop = document.getElementById('acPop');
  let activeInput = null, items = [], cursor = -1, lastQuery = '', timer = null;

  // The fragment being typed = text after the last comma/semicolon.
  function fragment(val){
    const m = val.split(/[,;]/);
    return m[m.length - 1].trim();
  }
  function replaceFragment(val, pick){
    const parts = val.split(/[,;]/);
    parts[parts.length - 1] = ' ' + pick;
    return parts.join(',').replace(/^\s*,?\s*/, '') + ', ';
  }
  function hide(){ pop.style.display = 'none'; items = []; cursor = -1; }
  function render(){
    if(!items.length){ hide(); return; }
    pop.innerHTML = items.map((c, i) =>
      `<div class="ac-item${i===cursor?' active':''}" data-i="${i}">`
      + `<span class="ac-name">${(c.name||'').replace(/</g,'&lt;')}</span> `
      + `<span class="ac-email">${c.email.replace(/</g,'&lt;')}</span></div>`
    ).join('');
    const r = activeInput.getBoundingClientRect();
    pop.style.position = 'fixed';
    pop.style.left = r.left + 'px';
    pop.style.top = (r.bottom + 2) + 'px';
    pop.style.width = r.width + 'px';
    pop.style.display = 'block';
  }
  function choose(i){
    const c = items[i]; if(!c) return;
    const pick = c.name ? `${c.name} <${c.email}>` : c.email;
    activeInput.value = replaceFragment(activeInput.value, pick);
    hide();
    activeInput.focus();
  }
  async function query(inp){
    const frag = fragment(inp.value);
    if(frag.length < 2){ hide(); return; }
    if(frag === lastQuery && pop.style.display === 'block') return;
    lastQuery = frag;
    try{
      const r = await fetch('/api/email2/contacts/suggest?q=' + encodeURIComponent(frag));
      const d = await r.json();
      if(activeInput !== inp) return;           // focus moved during await
      items = d.contacts || []; cursor = -1; render();
    }catch(e){ hide(); }
  }

  ['cmpTo','cmpCc','cmpBcc'].forEach(id => {
    const inp = document.getElementById(id);
    if(!inp) return;
    inp.addEventListener('input', () => {
      activeInput = inp;
      clearTimeout(timer);
      timer = setTimeout(() => query(inp), 150);
    });
    inp.addEventListener('keydown', (e) => {
      if(pop.style.display !== 'block' || !items.length) return;
      if(e.key === 'ArrowDown'){ e.preventDefault(); cursor = (cursor+1) % items.length; render(); }
      else if(e.key === 'ArrowUp'){ e.preventDefault(); cursor = (cursor-1+items.length) % items.length; render(); }
      else if(e.key === 'Enter'){ if(cursor >= 0){ e.preventDefault(); choose(cursor); } }
      else if(e.key === 'Escape'){ hide(); }
    });
    inp.addEventListener('blur', () => setTimeout(hide, 150)); // allow click to land
  });

  pop.addEventListener('mousedown', (e) => {       // mousedown beats blur
    const el = e.target.closest('.ac-item');
    if(el){ e.preventDefault(); choose(parseInt(el.dataset.i, 10)); }
  });
})();
```

- [ ] **Step 2: Add popover styling**

In the `<style>` section of `email.html`, add (if `.ac-pop` has no rules yet, add them; if it exists, ensure these are present):

```css
.ac-pop{ display:none; z-index:9999; background:#fff; border:1px solid #d0d3d9;
  border-radius:8px; box-shadow:0 6px 24px rgba(0,0,0,.15); max-height:260px;
  overflow:auto; font-size:13px; }
.ac-item{ padding:7px 10px; cursor:pointer; display:flex; gap:6px; align-items:baseline; }
.ac-item.active, .ac-item:hover{ background:#eef3ff; }
.ac-name{ font-weight:600; color:#1a1a1a; }
.ac-email{ color:#666; }
```

- [ ] **Step 3: Restart and verify in the browser**

```bash
sudo systemctl restart baza-dashboard
```
Then in the browser at the dashboard `/email` tab:
1. Click **New Message**, click into **To**, type at least 2 chars of a known client name or a past sender.
2. Expected: a dropdown of `Name email` suggestions appears under the field.
3. Click one (or arrow-down + Enter): the field fills with `Name <email>, `.
4. Type a comma and another fragment: suggestions appear for the new fragment only, prior recipient preserved.
5. Press Escape: dropdown closes.

- [ ] **Step 4: Commit**

```bash
cd ~/baza-empire/agent-framework-v3
git add dashboard/templates/email.html
git commit -m "feat(email): wire recipient typeahead into composer To/Cc/Bcc"
```

---

## Task 4: Log to session continuity + close out

- [ ] **Step 1: Append a continuity-log entry**

```bash
printf '\n### %s | D shipped — email recipient autocomplete\nWired #cmpTo/#cmpCc/#cmpBcc typeahead to /api/email2/contacts/suggest in email.html; backend _contact_suggestions now merges ahb_clients + email history (deduped, clients first); tests/test_email_contacts.py green; baza-dashboard restarted.\n' "$(date '+%Y-%m-%d %H:%M')" >> ~/Desktop/baza-session-log.md
```

- [ ] **Step 2: Final regression run**

Run: `cd ~/baza-empire/agent-framework-v3 && ./venv/bin/python -m pytest tests/test_email_contacts.py -q`
Expected: PASS

---

## Self-Review notes
- **Spec coverage (Piece D):** autocomplete wired (Task 3) ✓; endpoint exists and now also queries `ahb_clients` (Tasks 1–2) ✓; multi-recipient fragment handling (Task 3 `fragment`/`replaceFragment`) ✓.
- **Out of scope (per spec):** People/Contacts API, contact groups — not included. ✓
- **Naming consistency:** helper `_contact_suggestions(con, q, limit)` defined in Task 1, called identically in Task 2; JS ids `cmpTo/cmpCc/cmpBcc`, `acPop`, classes `ac-pop/ac-item/ac-name/ac-email` consistent across Task 3 steps. ✓
- **No placeholders:** all code shown in full. ✓
