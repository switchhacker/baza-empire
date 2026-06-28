# LinkedIn Social Connect (Track A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LinkedIn as a first-class publishing platform in the Social Studio Connections framework — OAuth paste-back connect, member (personal) + organization (Company Page) posting of an image or video, and an org feed.

**Architecture:** Extend `dashboard/social_connect.py` following its established platform pattern (boundary helpers `_li_*` that tests monkeypatch; connect → publish-dispatch → feed routes). LinkedIn media is *pushed* (register asset → PUT bytes → create post), so unlike Meta/TikTok it needs no public origin. Frontend adds one platform card + an account-picker modal in `templates/ahb123.html`.

**Tech Stack:** Python 3 / Flask, SQLite (`baza_projects.db`), `requests` (lazy-imported per existing pattern), pytest. Vanilla JS in the dashboard template.

---

## Conventions for this plan

- **Commits:** This repo is auto-committed+pushed hourly by `claw-auto-git` (see CLAUDE.md). **Do NOT run `git commit` manually.** Each task's checkpoint is a **green test run**, not a commit. An empty `git status` is not proof your edit failed — the timer may have committed it.
- **Run tests from repo root** `/home/switchhacker/baza-empire/agent-framework-v3` with the venv: `venv/bin/python -m pytest …`.
- **Test fixture:** new tests use the existing `env` fixture pattern from `tests/test_social_connect.py` (yields `(client, social_connect_module, db_path)`; redirects `ACCOUNTS_DIR`/`CREDS_DIR`/DB to a tmp dir; every `_li_*` network boundary is monkeypatched — **no network, no real creds**).
- **Dashboard restart:** `baza-dashboard.service` caches Jinja templates (`debug=False`). After editing `templates/ahb123.html` you MUST `sudo systemctl restart baza-dashboard` (Task 8).

## File structure

| File | Create/Modify | Responsibility |
|------|---------------|----------------|
| `dashboard/social_connect.py` | Modify | LinkedIn constants, `_li_*` boundary helpers, generalized OAuth start/finish, `/linkedin/add`, publish dispatch + `_publish_linkedin`, feed branch |
| `dashboard/templates/ahb123.html` | Modify | LinkedIn platform card + `connectLinkedIn` account-picker modal + `connectOAuth` branch |
| `tests/test_social_linkedin.py` | Create | Full TDD coverage of the backend, all boundaries monkeypatched |
| `docs/superpowers/specs/2026-06-27-linkedin-social-connect-design.md` | (exists) | Source spec |

---

### Task 1: Platform registration + LinkedIn constants

**Files:**
- Modify: `dashboard/social_connect.py:41-43` (PLATFORMS / OAUTH_PLATFORMS) and `:64-68` (add LinkedIn config block)
- Test: `tests/test_social_linkedin.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_social_linkedin.py` with the shared fixture (copied from `test_social_connect.py`) and the first test:

```python
"""Tests for Social Connections — LinkedIn (member + organization).

All LinkedIn network ops are monkeypatched; no credentials or network.
"""
import json
import os
import sqlite3
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SOCIAL_MODS = (
    "social_studio", "social_settings", "social_audio", "social_ai",
    "social_sources", "social_workflow", "social_trends", "social_analytics",
    "social_connect",
)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    db = os.path.join(str(tmp_path), "baza_projects.db")
    monkeypatch.setenv("BAZA_DASHBOARD_DB", db)
    monkeypatch.setenv("BAZA_SOCIAL_SETTINGS_DIR", str(tmp_path))
    sys.path.insert(0, os.path.join(REPO_ROOT, "dashboard"))
    for m in _SOCIAL_MODS:
        sys.modules.pop(m, None)
    import social_studio
    social_studio._ensure_social_tables(db)
    social_studio._ensure_social_v2_tables(db)
    social_connect = (sys.modules.get("dashboard.social_connect")
                      or sys.modules.get("social_connect"))
    assert social_connect is not None, "social_connect not loaded"
    monkeypatch.setattr(social_connect, "ACCOUNTS_DIR",
                        os.path.join(str(tmp_path), "accounts"))
    monkeypatch.setattr(social_connect, "CREDS_DIR",
                        os.path.join(str(tmp_path), "creds"))
    monkeypatch.setattr(social_connect, "EMAIL_CREDENTIALS_PATH",
                        os.path.join(str(tmp_path), "no-such-email-creds.json"))
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(social_studio.social_bp)
    yield app.test_client(), social_connect, db
    for m in _SOCIAL_MODS:
        sys.modules.pop(m, None)


def _make_post(db, asset_path=None, caption="hello", hashtags="#a #b",
               cover_path=None):
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "INSERT INTO ahb_social_posts (platform, variant, caption, hashtags, "
            "asset_path, cover_path, source_media_ids, status) VALUES "
            "('tiktok','9x16',?,?,?,?,'[]','draft')",
            (caption, hashtags, asset_path, cover_path),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _li_creds(sc):
    sc._secure_write(sc._platform_creds_path("linkedin"),
                     json.dumps({"client_id": "cid123", "client_secret": "sec"}))


def test_linkedin_registered_as_oauth_platform(env):
    c, sc, _ = env
    assert "linkedin" in sc.PLATFORMS
    assert "linkedin" in sc.OAUTH_PLATFORMS
    j = c.get("/api/ahb/social/connections").get_json()
    assert "linkedin" in j["platforms"]
    assert "linkedin" in j["oauth_platforms"]
    creds = c.get("/api/ahb/social/connections/app-creds").get_json()["configured"]
    assert creds["linkedin"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py::test_linkedin_registered_as_oauth_platform -v`
Expected: FAIL (`'linkedin' not in PLATFORMS`).

- [ ] **Step 3: Implement the constants**

In `dashboard/social_connect.py`, change line 41-43:

```python
PLATFORMS = ("youtube", "instagram", "facebook", "tiktok", "linkedin")
# Phase 1: only YouTube has a live OAuth + publish + feed path.
OAUTH_PLATFORMS = ("youtube", "linkedin")
```

After the TikTok block (after line 68, before `_VIDEO_EXTS` is fine, but keep it near the other platform configs — insert just after line 68):

```python
# LinkedIn — OAuth 2.0 authorization-code (paste-back, like YouTube). Member
# (personal profile) posting via w_member_social works self-serve; Company Page
# posting/org-feed need the Community Management API products (LinkedIn approval).
# Media is PUSHED to LinkedIn (register → PUT bytes), so no public URL is needed.
LINKEDIN_OAUTH_BASE = os.environ.get(
    "LINKEDIN_OAUTH_BASE", "https://www.linkedin.com/oauth/v2")
LINKEDIN_API_BASE = os.environ.get(
    "LINKEDIN_API_BASE", "https://api.linkedin.com")
# LinkedIn versions its REST API monthly (YYYYMM). Pin against current docs.
LINKEDIN_VERSION = os.environ.get("LINKEDIN_VERSION", "202401")
LI_SCOPES = [
    "openid", "profile", "email", "w_member_social",
    "w_organization_social", "r_organization_admin", "r_organization_social",
]
# Short-lived post-auth sessions (token + discovered member/orgs held server-side
# until the user picks a target). Mirrors _meta_sessions.
_linkedin_sessions: dict = {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py::test_linkedin_registered_as_oauth_platform -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint — full social suite green**

Run: `venv/bin/python -m pytest tests/ -k social -q`
Expected: all pass (existing + the one new test).

---

### Task 2: LinkedIn boundary helpers

**Files:**
- Modify: `dashboard/social_connect.py` (add `_li_*` helpers near the TikTok boundary block, before the `# storage helpers` section ~line 224)
- Test: `tests/test_social_linkedin.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_social_linkedin.py`:

```python
def test_li_client_creds_missing_raises(env):
    c, sc, _ = env
    with pytest.raises(RuntimeError):
        sc._li_client_creds()


def test_li_build_authorize_url(env):
    c, sc, _ = env
    _li_creds(sc)
    url = sc._li_build_authorize_url("st42")
    assert url.startswith("https://www.linkedin.com/oauth/v2/authorization")
    assert "client_id=cid123" in url
    assert "state=st42" in url
    assert "response_type=code" in url
    assert "w_member_social" in url  # scopes present (space-encoded)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "li_client_creds or build_authorize" -v`
Expected: FAIL (`module 'social_connect' has no attribute '_li_client_creds'`).

- [ ] **Step 3: Implement the helpers**

Insert this block in `dashboard/social_connect.py` immediately before the `# storage helpers` divider (~line 223). `requests` is lazy-imported inside each network helper, matching the `_yt_*`/`_meta_*`/`_tt_*` pattern.

```python
# ---------------------------------------------------------------------------
# LinkedIn boundary — monkeypatched in tests
# ---------------------------------------------------------------------------
def _li_client_creds() -> dict:
    """Read Serge's LinkedIn app client_id/client_secret JSON."""
    p = _platform_creds_path("linkedin")
    if not os.path.exists(p):
        raise RuntimeError(
            "No LinkedIn OAuth client configured. Add it in Connections "
            '(paste {"client_id":"…","client_secret":"…"}).')
    with open(p) as f:
        return json.load(f) or {}


def _li_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "LinkedIn-Version": LINKEDIN_VERSION,
            "X-Restli-Protocol-Version": "2.0.0"}


def _li_build_authorize_url(state: str) -> str:
    from urllib.parse import urlencode
    creds = _li_client_creds()
    params = {
        "response_type": "code",
        "client_id": creds.get("client_id", ""),
        "redirect_uri": OAUTH_REDIRECT_URI,
        "state": state,
        "scope": " ".join(LI_SCOPES),
    }
    return f"{LINKEDIN_OAUTH_BASE}/authorization?{urlencode(params)}"


def _li_exchange_token(code: str) -> dict:
    import requests
    creds = _li_client_creds()
    r = requests.post(
        f"{LINKEDIN_OAUTH_BASE}/accessToken",
        data={"grant_type": "authorization_code", "code": code,
              "redirect_uri": OAUTH_REDIRECT_URI,
              "client_id": creds.get("client_id", ""),
              "client_secret": creds.get("client_secret", "")},
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    r.raise_for_status()
    return r.json() or {}


def _li_userinfo(token: str) -> dict:
    import requests
    r = requests.get(f"{LINKEDIN_API_BASE}/v2/userinfo",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    d = r.json() or {}
    sub = d.get("sub", "")
    return {"person_urn": f"urn:li:person:{sub}" if sub else "",
            "name": d.get("name") or "LinkedIn account",
            "email": d.get("email", "")}


def _li_list_orgs(token: str) -> list:
    """Company Pages the member administers. Empty if Community Mgmt not approved."""
    import requests
    hdr = _li_headers(token)
    try:
        r = requests.get(f"{LINKEDIN_API_BASE}/rest/organizationAcls",
                         params={"q": "roleAssignee", "role": "ADMINISTRATOR",
                                 "state": "APPROVED"}, headers=hdr, timeout=30)
        r.raise_for_status()
    except Exception:
        return []
    out = []
    for el in (r.json() or {}).get("elements", []):
        org_urn = el.get("organization", "")
        if not org_urn:
            continue
        name = org_urn
        try:
            oid = org_urn.rsplit(":", 1)[-1]
            o = requests.get(f"{LINKEDIN_API_BASE}/rest/organizations/{oid}",
                             headers=hdr, timeout=30)
            if o.ok:
                name = (o.json() or {}).get("localizedName") or org_urn
        except Exception:
            pass
        out.append({"org_urn": org_urn, "name": name})
    return out


def _li_register_image(token: str, owner_urn: str) -> dict:
    import requests
    r = requests.post(f"{LINKEDIN_API_BASE}/rest/images?action=initializeUpload",
                      json={"initializeUploadRequest": {"owner": owner_urn}},
                      headers=_li_headers(token), timeout=30)
    r.raise_for_status()
    v = (r.json() or {}).get("value", {})
    return {"upload_url": v.get("uploadUrl", ""), "asset_urn": v.get("image", "")}


def _li_register_video(token: str, owner_urn: str, file_size: int) -> dict:
    import requests
    r = requests.post(f"{LINKEDIN_API_BASE}/rest/videos?action=initializeUpload",
                      json={"initializeUploadRequest": {
                          "owner": owner_urn, "fileSizeBytes": file_size,
                          "uploadCaptions": False, "uploadThumbnail": False}},
                      headers=_li_headers(token), timeout=30)
    r.raise_for_status()
    v = (r.json() or {}).get("value", {})
    instr = (v.get("uploadInstructions") or [{}])
    return {"upload_url": instr[0].get("uploadUrl", ""),
            "asset_urn": v.get("video", "")}


def _li_put_bytes(upload_url: str, path: str) -> str:
    """PUT asset bytes to LinkedIn's upload URL. Returns the response ETag."""
    import requests
    with open(path, "rb") as f:
        r = requests.put(upload_url, data=f.read(), timeout=300)
    r.raise_for_status()
    return r.headers.get("ETag", "")


def _li_finalize_video(token: str, asset_urn: str, etags: list) -> None:
    import requests
    r = requests.post(f"{LINKEDIN_API_BASE}/rest/videos?action=finalizeUpload",
                      json={"finalizeUploadRequest": {
                          "video": asset_urn, "uploadToken": "",
                          "uploadedPartIds": [e for e in etags if e]}},
                      headers=_li_headers(token), timeout=60)
    r.raise_for_status()


def _li_create_post(token: str, author_urn: str, commentary: str,
                    media_urn: str, is_video: bool) -> dict:
    import requests
    body = {
        "author": author_urn,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED",
                         "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if media_urn:
        body["content"] = {"media": {"id": media_urn}}
    r = requests.post(f"{LINKEDIN_API_BASE}/rest/posts", json=body,
                      headers=_li_headers(token), timeout=60)
    r.raise_for_status()
    post_id = r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or ""
    url = f"https://www.linkedin.com/feed/update/{post_id}" if post_id else ""
    return {"id": post_id, "url": url}


def _li_org_feed(token: str, org_urn: str, limit: int) -> list:
    import requests
    r = requests.get(f"{LINKEDIN_API_BASE}/rest/posts",
                     params={"q": "author", "author": org_urn, "count": limit},
                     headers=_li_headers(token), timeout=30)
    r.raise_for_status()
    out = []
    for el in (r.json() or {}).get("elements", []):
        pid = el.get("id", "")
        out.append({"id": pid, "title": (el.get("commentary") or "")[:80],
                    "published_at": str((el.get("createdAt") or "")),
                    "thumbnail": "",
                    "url": f"https://www.linkedin.com/feed/update/{pid}" if pid else ""})
    return out


def _li_token(account_ref: str) -> str:
    p = _token_path("linkedin", account_ref)
    if not os.path.exists(p):
        raise RuntimeError("token missing — reconnect the account")
    with open(p) as f:
        return (json.load(f) or {}).get("access_token", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "li_client_creds or build_authorize" -v`
Expected: PASS (both).

- [ ] **Step 5: Checkpoint — full social suite green**

Run: `venv/bin/python -m pytest tests/ -k social -q`
Expected: all pass.

---

### Task 3: Generalize `auth/start` for LinkedIn

**Files:**
- Modify: `dashboard/social_connect.py:470-497` (`social_auth_start`)
- Test: `tests/test_social_linkedin.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_li_auth_start_needs_creds(env):
    c, sc, _ = env
    r = c.post("/api/ahb/social/connections/linkedin/auth/start", json={})
    assert r.status_code == 400
    assert "LinkedIn" in r.get_json()["error"]


def test_li_auth_start_returns_url(env):
    c, sc, _ = env
    _li_creds(sc)
    r = c.post("/api/ahb/social/connections/linkedin/auth/start", json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["ok"] is True
    assert "linkedin.com" in j["auth_url"]
    assert j["flow_id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "auth_start" -v`
Expected: FAIL (linkedin path falls into `_yt_build_flow()` → error, not a LinkedIn URL).

- [ ] **Step 3: Implement the branch**

In `social_auth_start`, immediately after the `if platform not in OAUTH_PLATFORMS:` guard block (after line 479, before `try: flow = _yt_build_flow()`), insert:

```python
        if platform == "linkedin":
            state = _flow_id()
            try:
                auth_url = _li_build_authorize_url(state)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            flow_id = _flow_id()
            cutoff = time.time() - 3600
            for fid in [f for f, v in _oauth_flows.items()
                        if v.get("created", 0) < cutoff]:
                _oauth_flows.pop(fid, None)
            _oauth_flows[flow_id] = {
                "status": "pending", "platform": "linkedin",
                "state": state, "created": time.time(),
            }
            return jsonify({"ok": True, "flow_id": flow_id,
                            "auth_url": auth_url,
                            "redirect_uri": OAUTH_REDIRECT_URI})
```

(The existing YouTube `_yt_build_flow()` code below remains unchanged for the youtube path.)

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "auth_start" -v`
Expected: PASS (both).

- [ ] **Step 5: Checkpoint — full social suite green**

Run: `venv/bin/python -m pytest tests/ -k social -q`
Expected: all pass (YouTube auth_start test still green — proves the branch didn't break it).

---

### Task 4: `auth/finish` → discover member + orgs (no connection yet)

**Files:**
- Modify: `dashboard/social_connect.py:499-522` (`social_auth_finish`) and `:524-551` (`social_oauth_callback`, friendly LinkedIn page)
- Test: `tests/test_social_linkedin.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_li_auth_finish_returns_choices(env, monkeypatch):
    c, sc, _ = env
    _li_creds(sc)
    monkeypatch.setattr(sc, "_li_exchange_token", lambda code: {"access_token": "tok"})
    monkeypatch.setattr(sc, "_li_userinfo", lambda t: {
        "person_urn": "urn:li:person:abc", "name": "Serge T", "email": "s@x.z"})
    monkeypatch.setattr(sc, "_li_list_orgs", lambda t: [
        {"org_urn": "urn:li:organization:99", "name": "All Home Building"}])
    start = c.post("/api/ahb/social/connections/linkedin/auth/start",
                   json={}).get_json()
    fin = c.post("/api/ahb/social/connections/linkedin/auth/finish",
                 json={"flow_id": start["flow_id"],
                       "redirect_url": "http://localhost:8888/cb?code=thecode&state=x"})
    assert fin.status_code == 200, fin.get_data(as_text=True)
    j = fin.get_json()
    assert j["ok"] is True and j["ref"]
    assert j["member"]["person_urn"] == "urn:li:person:abc"
    assert j["orgs"][0]["org_urn"] == "urn:li:organization:99"
    # No connection created until the user picks a target.
    assert c.get("/api/ahb/social/connections").get_json()["items"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "auth_finish_returns_choices" -v`
Expected: FAIL (current finish calls `_finish_oauth` → google path → "unknown or expired flow").

- [ ] **Step 3: Implement the branch**

In `social_auth_finish`, after the code-extraction block (after line 517's `if not code: return …`), and BEFORE `result = _finish_oauth(flow_id, code)`, insert:

```python
        entry = _oauth_flows.get(flow_id) or {}
        if entry.get("platform") == "linkedin":
            try:
                tok = (_li_exchange_token(code) or {}).get("access_token", "")
                member = _li_userinfo(tok)
                orgs = _li_list_orgs(tok)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 502
            ref = _flow_id()
            cutoff = time.time() - 3600
            for k in [k for k, v in _linkedin_sessions.items()
                      if v.get("created", 0) < cutoff]:
                _linkedin_sessions.pop(k, None)
            _linkedin_sessions[ref] = {"token": tok, "member": member,
                                       "orgs": orgs, "created": time.time()}
            _oauth_flows.pop(flow_id, None)
            return jsonify({"ok": True, "ref": ref, "member": member,
                            "orgs": orgs})
```

Also harden `social_oauth_callback` (the GET LinkedIn lands on): after the `if not flow_id:` guard, add — so a real LinkedIn redirect shows guidance instead of the YouTube-only `_finish_oauth` error:

```python
        if _oauth_flows.get(flow_id, {}).get("platform") == "linkedin":
            return _page("✅ Signed in to LinkedIn",
                         "Copy this page's full URL and paste it back in the "
                         "Social tab to finish choosing your account.")
```

(Place it right after the `if error:` / `if not code:` guards, before `result = _finish_oauth(...)`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "auth_finish_returns_choices" -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint — full social suite green**

Run: `venv/bin/python -m pytest tests/ -k social -q`
Expected: all pass (YouTube `auth_finish_creates_connection` still green).

---

### Task 5: `/linkedin/add` — create member or org connection

**Files:**
- Modify: `dashboard/social_connect.py` (add route alongside `social_tiktok_token`, ~after line 781)
- Test: `tests/test_social_linkedin.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def _seed_li_session(sc):
    ref = sc._flow_id()
    sc._linkedin_sessions[ref] = {
        "token": "tok",
        "member": {"person_urn": "urn:li:person:abc", "name": "Serge T"},
        "orgs": [{"org_urn": "urn:li:organization:99", "name": "AHB"}],
        "created": time.time()}
    return ref


def test_li_add_member(env):
    c, sc, _ = env
    ref = _seed_li_session(sc)
    r = c.post("/api/ahb/social/connections/linkedin/add",
               json={"ref": ref, "target": "member"})
    assert r.status_code == 200, r.get_data(as_text=True)
    items = c.get("/api/ahb/social/connections").get_json()["items"]
    assert len(items) == 1
    assert items[0]["platform"] == "linkedin"
    assert items[0]["meta"]["person_urn"] == "urn:li:person:abc"
    tp = sc._token_path("linkedin", "urn:li:person:abc")
    assert os.path.exists(tp)
    assert oct(os.stat(tp).st_mode & 0o777) == "0o600"
    assert "token" not in json.dumps(items)  # never leak tokens


def test_li_add_org(env):
    c, sc, _ = env
    ref = _seed_li_session(sc)
    r = c.post("/api/ahb/social/connections/linkedin/add",
               json={"ref": ref, "target": "urn:li:organization:99"})
    assert r.status_code == 200, r.get_data(as_text=True)
    items = c.get("/api/ahb/social/connections").get_json()["items"]
    assert items[0]["meta"]["org_urn"] == "urn:li:organization:99"
    assert items[0]["account_label"] == "AHB"


def test_li_add_expired_session(env):
    c, sc, _ = env
    r = c.post("/api/ahb/social/connections/linkedin/add",
               json={"ref": "nope", "target": "member"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "li_add" -v`
Expected: FAIL (404 — route does not exist).

- [ ] **Step 3: Implement the route**

In `dashboard/social_connect.py`, after the `social_tiktok_token` route (after line 781, still inside `register(bp)`), add:

```python
    # ---- LinkedIn connect: pick member profile or an admined Company Page ----
    @bp.route("/api/ahb/social/connections/linkedin/add", methods=["POST"])
    def social_linkedin_add():
        data = request.get_json(silent=True) or {}
        ref = (data.get("ref") or "").strip()
        target = (data.get("target") or "").strip()  # "member" or an org URN
        sess = _linkedin_sessions.get(ref)
        if not sess:
            return jsonify({"error": "session expired — sign in again"}), 400
        token = sess.get("token") or ""
        if target == "member":
            m = sess.get("member") or {}
            urn = m.get("person_urn") or ""
            if not urn:
                return jsonify({"error": "no member profile on this session"}), 400
            label = m.get("name") or "LinkedIn profile"
            meta = {"person_urn": urn}
        else:
            org = next((o for o in (sess.get("orgs") or [])
                        if o.get("org_urn") == target), None)
            if not org:
                return jsonify({"error": "organization not in session"}), 404
            urn = target
            label = org.get("name") or target
            meta = {"org_urn": urn, "org_name": label}
        _secure_write(_token_path("linkedin", urn),
                      json.dumps({"access_token": token}))
        cid = _upsert_connection("linkedin", label, urn, " ".join(LI_SCOPES))
        _set_conn_meta(cid, meta)
        _linkedin_sessions.pop(ref, None)
        return jsonify({"ok": True, "connection_id": cid, "account_label": label})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "li_add" -v`
Expected: PASS (all three).

- [ ] **Step 5: Checkpoint — full social suite green**

Run: `venv/bin/python -m pytest tests/ -k social -q`
Expected: all pass.

---

### Task 6: Publish dispatch + `_publish_linkedin`

**Files:**
- Modify: `dashboard/social_connect.py:632-643` (publish dispatch) and add `_publish_linkedin` (module-level, near `_publish_tiktok` ~line 921)
- Test: `tests/test_social_linkedin.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def _seed_li_conn(sc, urn, meta, label="LI"):
    cid = sc._upsert_connection("linkedin", label, urn, " ".join(sc.LI_SCOPES))
    sc._set_conn_meta(cid, meta)
    sc._secure_write(sc._token_path("linkedin", urn),
                     json.dumps({"access_token": "tok"}))
    return cid


def test_li_publish_member_image(env, monkeypatch, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:person:abc", {"person_urn": "urn:li:person:abc"})
    img = tmp_path / "cover.jpg"; img.write_bytes(b"\xff\xd8\xff")
    pid = _make_post(db, asset_path=None, cover_path=str(img),
                     caption="New bath", hashtags="#remodel")
    monkeypatch.setattr(sc, "_li_register_image",
                        lambda t, o: {"upload_url": "U", "asset_urn": "urn:li:image:1"})
    cap = {}
    monkeypatch.setattr(sc, "_li_put_bytes",
                        lambda u, p: cap.update(put=p) or "etag")
    monkeypatch.setattr(sc, "_li_create_post",
                        lambda t, a, c2, m, v: {"id": "P1",
                        "url": "https://www.linkedin.com/feed/update/P1"})
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["url"].endswith("/P1")
    assert cap["put"] == str(img)
    con = sqlite3.connect(db)
    row = con.execute("SELECT status FROM ahb_social_posts WHERE id=?",
                      (pid,)).fetchone()
    con.close()
    assert row[0] == "posted"


def test_li_publish_org_video(env, monkeypatch, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:organization:99",
                        {"org_urn": "urn:li:organization:99"}, label="AHB")
    vid = tmp_path / "r.mp4"; vid.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    pid = _make_post(db, asset_path=str(vid), caption="Job", hashtags="#build")
    monkeypatch.setattr(sc, "_li_register_video",
                        lambda t, o, s: {"upload_url": "U", "asset_urn": "urn:li:video:2"})
    monkeypatch.setattr(sc, "_li_put_bytes", lambda u, p: "etag")
    monkeypatch.setattr(sc, "_li_finalize_video", lambda t, a, e: None)
    seen = {}
    monkeypatch.setattr(sc, "_li_create_post",
                        lambda t, a, c2, m, v: seen.update(author=a, media=m, video=v)
                        or {"id": "P2", "url": "https://x/P2"})
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert seen["author"] == "urn:li:organization:99"
    assert seen["media"] == "urn:li:video:2" and seen["video"] is True


def test_li_publish_no_asset(env, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:person:abc", {"person_urn": "urn:li:person:abc"})
    pid = _make_post(db, asset_path=None, cover_path=None)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 400
    assert "render" in r.get_json()["error"].lower()


def test_li_publish_org_pending_approval(env, monkeypatch, tmp_path):
    c, sc, db = env
    cid = _seed_li_conn(sc, "urn:li:organization:99",
                        {"org_urn": "urn:li:organization:99"}, label="AHB")
    img = tmp_path / "c.jpg"; img.write_bytes(b"\xff\xd8\xff")
    pid = _make_post(db, asset_path=None, cover_path=str(img))

    def boom(t, o):
        resp = type("R", (), {"status_code": 403})()
        e = Exception("403 Forbidden"); e.response = resp
        raise e
    monkeypatch.setattr(sc, "_li_register_image", boom)
    r = c.post(f"/api/ahb/social/posts/{pid}/publish",
               json={"connection_id": cid, "confirm": True})
    assert r.status_code == 403
    assert "Community Management" in r.get_json()["error"]
    assert "manual_export" in r.get_json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "li_publish" -v`
Expected: FAIL (linkedin hits the `platform != "youtube"` → 501 branch).

- [ ] **Step 3a: Implement the dispatch branch**

In `social_post_publish`, after the TikTok dispatch block (after line 638) and before `if platform != "youtube":`, insert:

```python
        if platform == "linkedin":
            body, code = _publish_linkedin(r, post, pid)
            return jsonify(body), code
```

- [ ] **Step 3b: Implement `_publish_linkedin`**

Add at module level near `_publish_tiktok` (after line 955):

```python
def _publish_linkedin(conn_row, post: dict, pid: int) -> tuple:
    """Publish a post to LinkedIn (member or org). Returns (body_dict, code).

    Media is pushed to LinkedIn (register → PUT bytes → create post) so no
    public origin is required.
    """
    meta = _get_conn_meta(conn_row)
    author_urn = meta.get("org_urn") or meta.get("person_urn") \
        or (conn_row["account_ref"] or "")
    is_org = author_urn.startswith("urn:li:organization")
    asset = post.get("asset_path")
    cover = post.get("cover_path")
    is_video = bool(asset and os.path.exists(asset)
                    and os.path.splitext(asset)[1].lower() in _VIDEO_EXTS)
    image_path = None
    if not is_video:
        if cover and os.path.exists(cover):
            image_path = cover
        elif asset and os.path.exists(asset) \
                and os.path.splitext(asset)[1].lower() in _IMAGE_EXTS:
            image_path = asset
    if not is_video and not image_path:
        return {"error": "post has no rendered image or video asset — "
                         "render it first."}, 400
    caption = post.get("caption") or ""
    hashtags = post.get("hashtags") or ""
    commentary = (caption + ("\n\n" + hashtags if hashtags else "")).strip()
    try:
        token = _li_token(conn_row["account_ref"] or "")
        if is_video:
            reg = _li_register_video(token, author_urn, os.path.getsize(asset))
            etag = _li_put_bytes(reg["upload_url"], asset)
            _li_finalize_video(token, reg["asset_urn"], [etag])
            media_urn = reg["asset_urn"]
        else:
            reg = _li_register_image(token, author_urn)
            _li_put_bytes(reg["upload_url"], image_path)
            media_urn = reg["asset_urn"]
        res = _li_create_post(token, author_urn, commentary, media_urn, is_video)
    except Exception as e:
        resp = getattr(e, "response", None)
        if is_org and resp is not None and getattr(resp, "status_code", 0) == 403:
            return {"error": "Company Page posting is pending LinkedIn Community "
                             "Management API approval — post to your personal "
                             "profile, or use Manual export.",
                    "manual_export": f"/api/ahb/social/posts/{pid}/manual-export"}, 403
        return {"error": str(e)}, 502
    url = res.get("url", "")
    _mark_posted(pid, url)
    return {"ok": True, "url": url, "platform": "linkedin"}, 200
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "li_publish" -v`
Expected: PASS (all four).

- [ ] **Step 5: Checkpoint — full social suite green**

Run: `venv/bin/python -m pytest tests/ -k social -q`
Expected: all pass (the existing `test_publish_unknown_platform_is_501` still green — LinkedIn no longer falls through to it but `threads` still does).

---

### Task 7: Feed for LinkedIn (org feed; member = friendly unavailable)

**Files:**
- Modify: `dashboard/social_connect.py:592-607` (feed dispatch in `social_conn_feed`)
- Test: `tests/test_social_linkedin.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_li_feed_org(env, monkeypatch):
    c, sc, _ = env
    cid = _seed_li_conn(sc, "urn:li:organization:99",
                        {"org_urn": "urn:li:organization:99"}, label="AHB")
    monkeypatch.setattr(sc, "_li_org_feed", lambda t, urn, lim: [
        {"id": "P1", "title": "hello", "url": "https://x/P1",
         "published_at": "1", "thumbnail": ""}])
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["items"][0]["id"] == "P1"


def test_li_feed_member_unavailable(env):
    c, sc, _ = env
    cid = _seed_li_conn(sc, "urn:li:person:abc", {"person_urn": "urn:li:person:abc"})
    r = c.get(f"/api/ahb/social/connections/{cid}/feed")
    assert r.status_code == 502
    assert "not available" in r.get_json()["error"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "li_feed" -v`
Expected: FAIL (linkedin hits the `else: # tiktok` branch → `_tt_video_list` on a linkedin token).

- [ ] **Step 3: Implement the branch**

In `social_conn_feed`, change the dispatch (lines 593-605) — add a `linkedin` branch before the `else: # tiktok`:

```python
            elif platform == "linkedin":
                if meta.get("org_urn"):
                    items = _li_org_feed(_li_token(r["account_ref"] or ""),
                                         meta["org_urn"], limit)
                else:
                    return jsonify({"error": "Personal-profile feed is not "
                                    "available via LinkedIn's API. Browse works "
                                    "for connected Company Pages."}), 502
            else:  # tiktok
                items = _tt_video_list(_tt_token(r["account_ref"] or ""), limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -k "li_feed" -v`
Expected: PASS (both).

- [ ] **Step 5: Checkpoint — full LinkedIn + social suite green**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py -q && venv/bin/python -m pytest tests/ -k social -q`
Expected: all pass.

---

### Task 8: Frontend — LinkedIn card + account-picker modal

**Files:**
- Modify: `dashboard/templates/ahb123.html` — PLATFORMS array (~line 21196), render action branch (~line 21243), `connectOAuth` (~line 21336), add `connectLinkedIn`, and the module export (~line 21514)
- Test: manual smoke (Task 9). No JS unit harness in this repo.

- [ ] **Step 1: Add the LinkedIn platform card**

In `SocialStudio.modules.connect.PLATFORMS` (after the tiktok entry, ~line 21195), add:

```javascript
    { id:'linkedin', name:'LinkedIn', icon:'💼', connect:'oauth', feed:true,
      note:'Posts to your profile now; the AHB123 Company Page activates once LinkedIn approves the Community Management API. Reuses LinkedIn sign-in.' },
```

- [ ] **Step 2: Route LinkedIn through the picker after OAuth**

In `connectOAuth`, the finish handler currently always closes on success. Replace the success tail of the `#sc-finish` click handler (lines 21334-21336) with a LinkedIn branch:

```javascript
      const fj = await fr.json();
      if (!fr.ok){ S.modules.toast.resolve(t2, 'error', fj.error || 'Failed'); return; }
      if (platform === 'linkedin') {
        S.modules.toast.resolve(t2, 'success', 'Signed in — pick an account');
        close();
        connectLinkedIn(fj);
        return;
      }
      S.modules.toast.resolve(t2, 'success', 'Connected ' + (fj.account_label||''));
      close(); render();
```

Also change the two Google-specific copy strings inside `connectOAuth` to be platform-neutral so LinkedIn reads correctly: the toast `'Opening Google…'` → `'Opening sign-in…'` (line 21310) and the modal step "1. A Google sign-in tab opened." → "1. A sign-in tab opened." (line 21318).

- [ ] **Step 3: Add `connectLinkedIn` (account picker)**

Add this function just after `connectTikTok` (before the module `return {…}` at ~line 21514), modeled on the Meta picker:

```javascript
  function connectLinkedIn(choices){
    const orgs = (choices && choices.orgs) || [];
    const member = (choices && choices.member) || {};
    const ref = choices && choices.ref;
    const { m, close } = _modal(`
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="font-size:16px;font-weight:800">Choose what to connect</div>
        <button data-close style="background:none;border:none;color:#666;cursor:pointer;font-size:20px">&times;</button>
      </div>
      <div style="font-size:12px;color:#aaa;margin-bottom:8px">Connect your personal profile, a Company Page you administer, or both (connect each separately).</div>
      <div id="sc-li-list"></div>
    `, true);
    const rows = [];
    if (member.person_urn){
      rows.push({ target:'member', label:(member.name||'My profile')+' · personal' });
    }
    orgs.forEach(o => rows.push({ target:o.org_urn, label:(o.name||o.org_urn)+' · Company Page' }));
    if (!rows.length){
      rows.push({ target:'member', label:'My LinkedIn profile' });
    }
    const box = m.querySelector('#sc-li-list');
    box.innerHTML = rows.map((r,i) => `
      <div style="display:flex;align-items:center;gap:8px;background:#070712;border:1px solid #1a1a2e;border-radius:6px;padding:8px;margin-bottom:6px">
        <div style="flex:1;font-size:12px;color:#ddd">${_esc(r.label)}</div>
        <button class="btn-secondary sc-li-pick" data-i="${i}" style="font-size:11px;padding:4px 10px">Connect</button>
      </div>`).join('');
    box.querySelectorAll('.sc-li-pick').forEach(btn => {
      btn.addEventListener('click', async () => {
        const r = rows[parseInt(btn.dataset.i,10)];
        const tid = S.modules.toast.progress('Connecting…');
        const ar = await fetch('/api/ahb/social/connections/linkedin/add', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ ref, target: r.target }),
        });
        const aj = await ar.json();
        if (!ar.ok){ S.modules.toast.resolve(tid, 'error', aj.error || 'Failed'); return; }
        S.modules.toast.resolve(tid, 'success', 'Connected ' + (aj.account_label||''));
        close(); render();
      });
    });
  }
```

- [ ] **Step 4: Export `connectLinkedIn`**

In the module return (line ~21514), add `connectLinkedIn`:

```javascript
  return { render, setAppCreds, connectOAuth, connectMeta, connectTikTok, connectLinkedIn, disconnect, browseFeed, publishPicker, manualExport };
```

- [ ] **Step 5: Restart the dashboard (template cache)**

Run: `sudo systemctl restart baza-dashboard`
Expected: command returns 0. (Jinja caches templates; the edit won't show otherwise.)

---

### Task 9: Live smoke test + session log

**Files:**
- No code. Verification + `~/Desktop/baza-session-log.md`.

- [ ] **Step 1: Full backend suite**

Run: `venv/bin/python -m pytest tests/test_social_linkedin.py tests/test_social_connect.py -q`
Expected: all pass (LinkedIn suite ~14 tests + existing YouTube/social suite).

- [ ] **Step 2: Live endpoint smoke (no creds needed)**

Run:
```bash
curl -s localhost:8888/api/ahb/social/connections | python3 -c "import sys,json;d=json.load(sys.stdin);print('linkedin' in d['oauth_platforms'])"
curl -s localhost:8888/api/ahb/social/connections/app-creds | python3 -c "import sys,json;print('linkedin' in json.load(sys.stdin)['configured'])"
```
Expected: `True` then `True`.

- [ ] **Step 3: Visual check**

Open the dashboard Social tab → Connections. Confirm a **LinkedIn 💼** card renders with an "Add LinkedIn OAuth client" button (since no creds yet). Clicking it opens the credentials modal.

- [ ] **Step 4: Append session-log entry**

Run (get the timestamp from `date`, never guess):
```bash
printf '\n### %s | LinkedIn social connect (Track A) shipped\n- Added linkedin platform to social_connect.py: OAuth paste-back (member + org), _li_* boundary helpers, /linkedin/add picker, _publish_linkedin (image/video push — no public URL), org feed. Frontend LinkedIn card + connectLinkedIn account picker in ahb123.html. tests/test_social_linkedin.py (~14, all monkeypatched) green; baza-dashboard restarted. Prereq for live use: Serge creates LinkedIn dev app + adds client creds in Connections (member posts now; Company Page on Community Management API approval). Next: Track B (Thumbtack/Angi lead intake).\n' "$(date '+%Y-%m-%d %H:%M')" >> ~/Desktop/baza-session-log.md
```

---

## Self-review notes (author)

- **Spec coverage:** §4.1 constants→T1; §4.2 helpers→T2; §4.3 finish generalization→T4; §4.4 routes (auth/start→T3, add→T5, publish→T6, feed→T7)→covered; §4.5 `_publish_linkedin`→T6; §5 frontend→T8; §6 tests→T1-7; §3 prereq→surfaced in T9 + card copy. ✓
- **No placeholders:** every code/test step is complete and runnable.
- **Type/name consistency:** helper names (`_li_register_image/video`, `_li_put_bytes`, `_li_finalize_video`, `_li_create_post`, `_li_org_feed`, `_li_token`, `_li_client_creds`, `_li_build_authorize_url`, `_li_exchange_token`, `_li_userinfo`, `_li_list_orgs`) are defined in T2 and used identically in T3-T7. Session dict `_linkedin_sessions` defined T1, used T4/T5. Connection meta keys `person_urn`/`org_urn` consistent across T5/T6/T7.
- **Known follow-ups (out of v1):** large multi-part video upload (T2 `_li_register_video` reads the first upload instruction only — fine for single-part; multi-part is a follow-up), member-feed reading (LinkedIn-restricted), scheduling/analytics/carousels. The `LINKEDIN_VERSION` value and exact `rest/posts` field names must be re-confirmed against current LinkedIn docs at implementation time.
