# Email Unified Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "All inboxes" view that merges the INBOX of every connected Gmail account into one chronological list, with each thread acting on its own account.

**Architecture:** Extend the existing `/api/email2/threads` and `/api/email2/search` endpoints to accept `account=ALL`; stamp every thread/result with its `account_id`/`account_email`; add an "All inboxes" pseudo-account to the account pill in `email.html` and thread the per-thread account through open/reply/modify.

**Tech Stack:** Flask blueprint (`email_studio.py`), vanilla JS template (`email.html`), Gmail API client, SQLite cache (`baza_projects.db` emails table).

**Spec:** `docs/superpowers/specs/2026-06-16-ahb123-email-unified-inbox-design.md`

**Repo note:** Do NOT manually `git commit` — `claw-auto-git` commits `agent-framework-v3` hourly. The per-task checkpoint is "tests green". After editing `email.html`, `sudo systemctl restart baza-dashboard` (Jinja cache).

**Test location:** `dashboard/tests/test_email_unified.py` (new). Use the Flask test-client pattern already used by the social blueprint tests; mock `_gmail` and the Gmail service so no live API call is made.

---

### Task 1: Extract `_hydrate_thread` helper (no behavior change)

**Files:**
- Modify: `dashboard/email_studio.py` (the per-thread hydration loop inside `api_threads`, ~lines 467–514)
- Test: `dashboard/tests/test_email_unified.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/tests/test_email_unified.py
import email_studio

def test_hydrate_thread_stamps_account(monkeypatch):
    # Cached row present -> uses cache, stamps account fields
    class FakeCon:
        def execute(self, *a, **k):
            class R:
                def fetchone(self_inner): return None  # force remote path
            return R()
    class FakeSvc:
        def users(self): return self
        def threads(self): return self
        def get(self, **k):
            class E:
                def execute(self_inner):
                    return {"messages": [{"labelIds": ["INBOX"],
                            "payload": {"headers": [
                                {"name": "From", "value": "a@b.com"},
                                {"name": "Subject", "value": "Hi"},
                                {"name": "Date", "value": "2026-06-16"}]}}]}
            return E()
    t = {"id": "T1", "snippet": "snip"}
    out = email_studio._hydrate_thread(FakeSvc(), FakeCon(), t, "acc-1", "me@x.com")
    assert out["thread_id"] == "T1"
    assert out["account_id"] == "acc-1"
    assert out["account_email"] == "me@x.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_email_unified.py::test_hydrate_thread_stamps_account -v`
Expected: FAIL with `AttributeError: module 'email_studio' has no attribute '_hydrate_thread'`

- [ ] **Step 3: Extract the helper**

In `email_studio.py`, create a module-level function containing the exact body currently inside the `for t in threads:` loop of `api_threads` (the cache-lookup + remote-metadata-fetch that builds the per-thread dict). Signature:

```python
def _hydrate_thread(svc, con, t, account_id, account_email):
    tid = t["id"]
    row = con.execute(
        """SELECT thread_id, subject, from_addr, to_addr, body_snippet,
                  received_at, labels, is_unread, is_starred, ai_summary,
                  category, gmail_id
           FROM emails WHERE thread_id=? ORDER BY received_at DESC LIMIT 1""",
        (tid,)
    ).fetchone()
    if row:
        d = dict(row)
        out = {
            "thread_id": tid,
            "subject": d["subject"] or "(no subject)",
            "from": d["from_addr"] or "",
            "snippet": d["body_snippet"] or t.get("snippet", ""),
            "received_at": d["received_at"] or "",
            "labels": (d["labels"] or "").split(",") if d["labels"] else [],
            "is_unread": bool(d["is_unread"]),
            "is_starred": bool(d["is_starred"]),
            "ai_summary": d["ai_summary"] or "",
            "category": d["category"] or "",
            "cached": True,
        }
    else:
        msg = svc.users().threads().get(
            userId="me", id=tid, format="metadata",
            metadataHeaders=["From", "Subject", "Date", "To"]
        ).execute()
        msgs = msg.get("messages", []) or []
        head = msgs[-1] if msgs else {}
        hdrs = _headers_map(head)
        labels = head.get("labelIds", []) or []
        out = {
            "thread_id": tid,
            "subject": hdrs.get("Subject", "(no subject)"),
            "from": hdrs.get("From", ""),
            "snippet": t.get("snippet", ""),
            "received_at": hdrs.get("Date", ""),
            "labels": labels,
            "is_unread": "UNREAD" in labels,
            "is_starred": "STARRED" in labels,
            "ai_summary": "",
            "category": "",
            "cached": False,
        }
    out["account_id"] = account_id
    out["account_email"] = account_email
    return out
```

Then replace the loop in `api_threads` with:

```python
acc = _pick_account(_req_account_id())
acc_id = acc["id"] if acc else None
acc_email = acc["email"] if acc else ""
out = [_hydrate_thread(svc, con, t, acc_id, acc_email) for t in threads]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_email_unified.py::test_hydrate_thread_stamps_account -v`
Expected: PASS

- [ ] **Step 5: Checkpoint** — tests green; manual smoke of single-account thread list still works after `sudo systemctl restart baza-dashboard`.

---

### Task 2: `api_threads` honors `account=ALL` (merge all inboxes)

**Files:**
- Modify: `dashboard/email_studio.py` (`api_threads`, ~443–522)
- Test: `dashboard/tests/test_email_unified.py`

- [ ] **Step 1: Write the failing test**

```python
def test_threads_all_merges_and_sorts(monkeypatch, client):
    # Two accounts; each returns one thread. Merged result is sorted desc by received_at.
    accounts = [{"id": "a1", "email": "one@x.com"}, {"id": "a2", "email": "two@x.com"}]
    monkeypatch.setattr(email_studio, "_all_accounts", lambda: accounts)
    def fake_gmail(aid=None):
        class Svc:
            def users(self): return self
            def threads(self): return self
            def list(self, **k):
                class E:
                    def execute(self_inner):
                        return {"threads": [{"id": "t-" + aid, "snippet": "s"}]}
                return E()
        return Svc()
    monkeypatch.setattr(email_studio, "_gmail", fake_gmail)
    monkeypatch.setattr(email_studio, "_hydrate_thread",
        lambda svc, con, t, aid, ae: {"thread_id": t["id"], "account_id": aid,
            "account_email": ae, "received_at": "2026-06-1" + ("5" if aid == "a1" else "6")})
    r = client.get("/api/email2/threads?account=ALL&limit=10")
    data = r.get_json()
    ids = [t["thread_id"] for t in data["threads"]]
    assert ids == ["t-a2", "t-a1"]  # a2 newer -> first
    assert {t["account_id"] for t in data["threads"]} == {"a1", "a2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_email_unified.py::test_threads_all_merges_and_sorts -v`
Expected: FAIL (`_all_accounts` missing, and `account=ALL` not handled)

- [ ] **Step 3: Implement**

Add a helper near `_active_account`:

```python
def _all_accounts():
    con = _conn()
    try:
        rows = con.execute("SELECT * FROM email_accounts ORDER BY email ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()
```

In `api_threads`, immediately after reading `label`/`q`/`limit`/`page_token`, branch:

```python
if _req_account_id() == "ALL":
    return _threads_all(limit, q)
```

Add `_threads_all`:

```python
def _threads_all(limit, q):
    merged = []
    con = _conn()
    try:
        for acc in _all_accounts():
            try:
                svc = _gmail(acc["id"])
                kwargs = {"userId": "me", "maxResults": limit, "labelIds": ["INBOX"]}
                if q:
                    kwargs["q"] = q
                resp = svc.users().threads().list(**kwargs).execute()
                for t in resp.get("threads", []) or []:
                    merged.append(_hydrate_thread(svc, con, t, acc["id"], acc["email"]))
            except Exception as e:
                print(f"[email] ALL-inbox fetch failed for {acc.get('email')}: {e}", flush=True)
                continue
    finally:
        con.close()
    merged.sort(key=lambda x: x.get("received_at") or "", reverse=True)
    return jsonify({"threads": merged[:limit], "next_page_token": None})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_email_unified.py::test_threads_all_merges_and_sorts -v`
Expected: PASS

- [ ] **Step 5: Add a "one account fails" test and verify**

```python
def test_threads_all_skips_failing_account(monkeypatch, client):
    accounts = [{"id": "a1", "email": "one@x.com"}, {"id": "a2", "email": "two@x.com"}]
    monkeypatch.setattr(email_studio, "_all_accounts", lambda: accounts)
    def fake_gmail(aid=None):
        if aid == "a1":
            raise RuntimeError("bad token")
        class Svc:
            def users(self): return self
            def threads(self): return self
            def list(self, **k):
                class E:
                    def execute(self_inner): return {"threads": [{"id": "t-a2"}]}
                return E()
        return Svc()
    monkeypatch.setattr(email_studio, "_gmail", fake_gmail)
    monkeypatch.setattr(email_studio, "_hydrate_thread",
        lambda svc, con, t, aid, ae: {"thread_id": t["id"], "account_id": aid, "received_at": "x"})
    r = client.get("/api/email2/threads?account=ALL&limit=10")
    assert [t["thread_id"] for t in r.get_json()["threads"]] == ["t-a2"]
```

Run: `cd dashboard && python -m pytest tests/test_email_unified.py -v`
Expected: PASS (all)

- [ ] **Step 6: Checkpoint** — tests green.

---

### Task 3: `api_search` — account scoping + `account_id` in results

**Files:**
- Modify: `dashboard/email_studio.py` (`api_search`, ~1032–1070)
- Test: `dashboard/tests/test_email_unified.py`

- [ ] **Step 1: Write the failing test**

```python
def test_search_scopes_to_account_and_returns_account_id(client, seed_emails):
    # seed_emails fixture inserts rows: gmail_id g1(acc a1), g2(acc a2), g3(acc NULL)
    r_all = client.get("/api/email2/search?q=invoice&account=ALL").get_json()
    accs = {row["account_id"] for row in r_all["results"]}
    assert "a1" in accs and "a2" in accs
    r_one = client.get("/api/email2/search?q=invoice&account=a1").get_json()
    got = {row["account_id"] for row in r_one["results"]}
    assert got <= {"a1", None}  # a1 + legacy NULL only, never a2
```

(Provide a `seed_emails` fixture in the test file that creates the `emails`/`emails_fts` rows with `account_id` a1, a2, NULL and a body containing "invoice".)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_email_unified.py::test_search_scopes_to_account_and_returns_account_id -v`
Expected: FAIL (no `account_id` in results; no account filter)

- [ ] **Step 3: Implement**

In `api_search`, after computing `safe`, resolve the account and build the SQL with an optional filter:

```python
acct = _req_account_id()
base = """SELECT e.thread_id, e.gmail_id, e.subject, e.from_addr, e.body_snippet,
                 e.received_at, e.labels, e.is_unread, e.is_starred, e.account_id,
                 a.email AS account_email, bm25(emails_fts) AS rank
          FROM emails_fts JOIN emails e ON e.gmail_id = emails_fts.gmail_id
          LEFT JOIN email_accounts a ON a.id = e.account_id
          WHERE emails_fts MATCH ?"""
params = [f'"{safe}"']
if acct and acct != "ALL":
    base += " AND (e.account_id = ? OR e.account_id IS NULL)"
    params.append(acct)
base += " ORDER BY rank LIMIT ?"
params.append(limit)
rows = con.execute(base, params).fetchall()
```

Add `account_id` and `account_email` to each appended result dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_email_unified.py -v`
Expected: PASS

- [ ] **Step 5: Checkpoint** — tests green.

---

### Task 4: Frontend — "All inboxes" pseudo-account + request plumbing

**Files:**
- Modify: `dashboard/templates/email.html` (`renderAccountMenu` ~1072, `loadThreads` ~640, `searchThreads`/search caller ~1003, `loadLabels` ~613)

- [ ] **Step 1: Add the menu entry** — in `renderAccountMenu`, prepend an "All inboxes" row before the per-account rows:

```javascript
const allRow = `
  <div class="acc-menu-item ${state.activeAccount?.all?'active':''}" onclick="selectAllInboxes()">
    <div class="acc-avatar" style="width:22px;height:22px;font-size:10px">📬</div>
    <div style="flex:1;min-width:0">
      <div style="font-size:11.5px;font-weight:700;color:#e0e0e0">All inboxes</div>
      <div style="font-size:9.5px;color:#5a5e7a">Merged · every account</div>
    </div>
  </div>`;
menu.innerHTML = allRow + (rows || '') +
  '<div class="acc-menu-divider"></div>' +
  '<div class="acc-menu-add" onclick="openAddAccount()">➕ Add Gmail account</div>';
```

- [ ] **Step 2: Add `selectAllInboxes` + remember last real account**

```javascript
function selectAllInboxes(){
  if(state.activeAccount && !state.activeAccount.all) state.lastRealAccount = state.activeAccount;
  state.activeAccount = {id:'ALL', email:'All inboxes', all:true};
  document.getElementById('accMenu').classList.remove('open');
  renderAccountPill();
  state.currentThreadId = null; state.currentThread = null;
  document.getElementById('readerEmpty').style.display = 'flex';
  document.getElementById('readerContent').style.display = 'none';
  loadLabels(); loadThreads();
}
```

In `renderAccountPill`, when `state.activeAccount?.all`, set avatar "📬", email "All inboxes", label "Merged".
In `switchAccount` (real accounts), also set `state.lastRealAccount` to the activated account.

- [ ] **Step 3: Request plumbing** — in `loadThreads`, build the URL with the account:

```javascript
const acc = state.activeAccount?.all ? 'ALL' : (state.activeAccount?.id || '');
if(q){ url = '/api/email2/search?q=' + encodeURIComponent(q) + (acc?('&account='+encodeURIComponent(acc)):''); }
else  { url = '/api/email2/threads?label=' + encodeURIComponent(state.label) +
              '&limit=40' + (acc?('&account='+encodeURIComponent(acc)):''); }
```

In `loadLabels`, when `state.activeAccount?.all`, skip the user-labels group (system Mailboxes only) — guard the `data.user` branch with `&& !state.activeAccount?.all`.

- [ ] **Step 4: Manual verify** — `sudo systemctl restart baza-dashboard`; open `/email`, pick "All inboxes": threads from ≥2 accounts appear newest-first; the pill shows "All inboxes". (No automated test for template JS; verify in browser.)

- [ ] **Step 5: Checkpoint.**

---

### Task 5: Frontend — per-thread account on open/reply/modify + account badge

**Files:**
- Modify: `dashboard/templates/email.html` (`renderThreads` ~660, `openThread` ~698, reply/send callers ~929/962, mark-read modify ~716)

- [ ] **Step 1: Helper to find a thread's account**

```javascript
function threadAccount(tid){
  const t = state.threads.find(x => x.thread_id === tid);
  return t && t.account_id ? t.account_id : (state.activeAccount && !state.activeAccount.all ? state.activeAccount.id : '');
}
function acctParam(tid){ const a = threadAccount(tid); return a ? ('&account='+encodeURIComponent(a)) : ''; }
```

- [ ] **Step 2: Thread open + mark-read use the thread's account** — in `openThread(tid)`:

```javascript
const data = await api('/api/email2/thread/' + tid + '?account=' + encodeURIComponent(threadAccount(tid)));
...
await api('/api/email2/modify',{method:'POST',body:{target:'thread',id:tid,action:'read',account:threadAccount(tid)}});
```

In reply/send (`api('/api/email2/send', ...)`), add `account: threadAccount(state.currentThreadId)` to the body. For brand-new compose use `state.lastRealAccount?.id` if set.

- [ ] **Step 3: Account badge in `renderThreads`** — when `state.activeAccount?.all`, add a badge per row:

```javascript
const badge = state.activeAccount?.all && t.account_email
  ? `<span class="acct-badge" style="font-size:9px;padding:1px 5px;border-radius:8px;background:#13132a;color:#7a7ea0;margin-left:6px">${esc(t.account_email.split('@')[0])}</span>`
  : '';
```

Insert `${badge}` next to the from-name in `thread-row1`.

- [ ] **Step 4: Manual verify** — In "All inboxes": each row shows its source-account badge; opening a thread from account B reads/marks-read/replies via account B (confirm reply lands in B's Sent). `sudo systemctl restart baza-dashboard` first.

- [ ] **Step 5: Final checkpoint** — run `cd dashboard && python -m pytest tests/test_email_unified.py -v` (all green) and confirm single-account mode still behaves (regression smoke).
