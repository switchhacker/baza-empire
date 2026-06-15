"""Social Connections — Phase 1.

Connect social accounts and post composed content directly from the Social tab,
plus a universal manual-export fallback that works before any platform app is
approved.

Phase 1 ships the full framework + storage + YouTube (OAuth/publish/feed),
which reuses the same Google paste-back OAuth flow the email studio uses.
Instagram / Facebook / TikTok are wired as connection rows but their publish/
feed land in Phase 2/3; until then those platforms use manual export.

Hard rules honored here:
- Tokens live on disk (perms 600) under social-pipeline/, never in the DB,
  never returned by list endpoints.
- Publish is outward-facing → requires an explicit `confirm` flag.
- App client secrets are provided by Serge; nothing is hard-coded.

All real Google network operations sit behind module-level helpers
(`_yt_*`, `_load_creds`) so tests can monkeypatch them.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Optional

from flask import jsonify, request, send_file

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_DIR = os.path.dirname(DASHBOARD_DIR)
SOCIAL_PIPELINE_DIR = os.path.join(FRAMEWORK_DIR, "social-pipeline")
ACCOUNTS_DIR = os.path.join(SOCIAL_PIPELINE_DIR, "accounts")
CREDS_DIR = os.path.join(SOCIAL_PIPELINE_DIR, "credentials")
# Google OAuth client falls back to the email pipeline's client if no
# YouTube-specific one is configured (same GCP project can serve both once the
# YouTube Data API + scope are enabled on the consent screen).
EMAIL_CREDENTIALS_PATH = os.path.join(FRAMEWORK_DIR, "email-pipeline", "credentials.json")

PLATFORMS = ("youtube", "instagram", "facebook", "tiktok")
# Phase 1: only YouTube has a live OAuth + publish + feed path.
OAUTH_PLATFORMS = ("youtube",)

YT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
OAUTH_REDIRECT_URI = os.environ.get(
    "SOCIAL_OAUTH_REDIRECT",
    "http://localhost:8888/api/ahb/social/connections/oauth/callback",
)
# Google sometimes returns extra scopes (openid, …); don't fail the exchange.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# Phase 2: Meta (Instagram + Facebook Page) connect via a pasted long-lived
# access token (no redirect-based OAuth needed for single-user local use).
META_GRAPH = os.environ.get("META_GRAPH_BASE", "https://graph.facebook.com/v19.0")
# Phase 3: TikTok Content Posting API. Unaudited apps can only post privately
# (SELF_ONLY drafts); public direct-post requires TikTok to audit the app.
TIKTOK_API = os.environ.get("TIKTOK_API_BASE", "https://open.tiktokapis.com/v2")
TOKEN_PLATFORMS = ("instagram", "facebook")
# Instagram/Facebook pull media from a PUBLIC url — set this to the https origin
# that fronts baza (e.g. https://ahb123.com) so the Graph API can fetch assets.
SOCIAL_PUBLIC_BASE_URL = os.environ.get("SOCIAL_PUBLIC_BASE_URL", "")

# In-memory OAuth flow registry (mirrors email_studio).
_oauth_flows: dict = {}
# Short-lived Meta token-discovery sessions (page list held server-side so page
# tokens never round-trip through the browser).
_meta_sessions: dict = {}


def _public_base() -> str:
    return (SOCIAL_PUBLIC_BASE_URL or "").rstrip("/")


def _public_asset_url(pid: int, kind: str) -> str:
    base = _public_base()
    if not base:
        return ""
    path = (f"/api/ahb/social/posts/{pid}/asset" if kind == "video"
            else f"/api/ahb/social/posts/{pid}/cover")
    return base + path


# ---------------------------------------------------------------------------
# Meta Graph boundary — monkeypatched in tests
# ---------------------------------------------------------------------------
def _meta_list_pages(user_token: str) -> list:
    import requests
    r = requests.get(
        f"{META_GRAPH}/me/accounts",
        params={"access_token": user_token,
                "fields": "id,name,access_token,instagram_business_account{id,username}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def _meta_publish_photo(page_id, page_token, image_url, caption) -> dict:
    import requests
    r = requests.post(f"{META_GRAPH}/{page_id}/photos",
                      data={"url": image_url, "caption": caption,
                            "access_token": page_token}, timeout=120)
    r.raise_for_status()
    j = r.json()
    pid_ = j.get("post_id") or j.get("id", "")
    return {"id": pid_, "url": f"https://facebook.com/{pid_}" if pid_ else ""}


def _meta_publish_video(page_id, page_token, video_url, caption) -> dict:
    import requests
    r = requests.post(f"{META_GRAPH}/{page_id}/videos",
                      data={"file_url": video_url, "description": caption,
                            "access_token": page_token}, timeout=180)
    r.raise_for_status()
    vid = r.json().get("id", "")
    return {"id": vid, "url": f"https://facebook.com/{vid}" if vid else ""}


def _meta_ig_publish(ig_id, token, media_url, caption, is_video) -> dict:
    import requests
    params = {"caption": caption, "access_token": token}
    if is_video:
        params["media_type"] = "REELS"
        params["video_url"] = media_url
    else:
        params["image_url"] = media_url
    c = requests.post(f"{META_GRAPH}/{ig_id}/media", data=params, timeout=180)
    c.raise_for_status()
    creation_id = c.json().get("id")
    p = requests.post(f"{META_GRAPH}/{ig_id}/media_publish",
                      data={"creation_id": creation_id, "access_token": token},
                      timeout=120)
    p.raise_for_status()
    mid = p.json().get("id", "")
    return {"id": mid, "url": f"https://instagram.com/p/{mid}" if mid else "",
            "creation_id": creation_id}


def _meta_page_feed(page_id, token, limit) -> list:
    import requests
    r = requests.get(f"{META_GRAPH}/{page_id}/posts",
                     params={"access_token": token, "limit": limit,
                             "fields": "id,message,created_time,full_picture,permalink_url"},
                     timeout=20)
    r.raise_for_status()
    out = []
    for it in r.json().get("data", []):
        out.append({"id": it.get("id"), "title": (it.get("message") or "")[:80],
                    "published_at": it.get("created_time", ""),
                    "thumbnail": it.get("full_picture", ""),
                    "url": it.get("permalink_url", "")})
    return out


def _meta_ig_media(ig_id, token, limit) -> list:
    import requests
    r = requests.get(f"{META_GRAPH}/{ig_id}/media",
                     params={"access_token": token, "limit": limit,
                             "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp"},
                     timeout=20)
    r.raise_for_status()
    out = []
    for it in r.json().get("data", []):
        out.append({"id": it.get("id"), "title": (it.get("caption") or "")[:80],
                    "published_at": it.get("timestamp", ""),
                    "thumbnail": it.get("thumbnail_url") or it.get("media_url") or "",
                    "url": it.get("permalink", "")})
    return out


# ---------------------------------------------------------------------------
# TikTok boundary — monkeypatched in tests
# ---------------------------------------------------------------------------
def _tt_headers(token):
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8"}


def _tt_creator_info(access_token) -> dict:
    import requests
    r = requests.post(f"{TIKTOK_API}/post/publish/creator_info/query/",
                      headers=_tt_headers(access_token), timeout=20)
    r.raise_for_status()
    return (r.json() or {}).get("data", {})


def _tt_publish_video(access_token, video_url, title, privacy_level) -> dict:
    import requests
    body = {
        "post_info": {"title": title[:150], "privacy_level": privacy_level,
                      "disable_comment": False, "disable_duet": False,
                      "disable_stitch": False},
        "source_info": {"source": "PULL_FROM_URL", "video_url": video_url},
    }
    r = requests.post(f"{TIKTOK_API}/post/publish/video/init/",
                      headers=_tt_headers(access_token), json=body, timeout=60)
    r.raise_for_status()
    return {"publish_id": ((r.json() or {}).get("data", {})).get("publish_id", "")}


def _tt_video_list(access_token, limit) -> list:
    import requests
    r = requests.post(
        f"{TIKTOK_API}/video/list/?fields=id,title,cover_image_url,share_url,create_time",
        headers=_tt_headers(access_token), json={"max_count": min(limit, 20)},
        timeout=20)
    r.raise_for_status()
    out = []
    for it in (r.json() or {}).get("data", {}).get("videos", []):
        out.append({"id": it.get("id"), "title": (it.get("title") or "")[:80],
                    "published_at": str(it.get("create_time", "")),
                    "thumbnail": it.get("cover_image_url", ""),
                    "url": it.get("share_url", "")})
    return out


# ---------------------------------------------------------------------------
# storage helpers
# ---------------------------------------------------------------------------
def _db_path() -> str:
    return os.environ.get(
        "BAZA_DASHBOARD_DB", os.path.join(DASHBOARD_DIR, "baza_projects.db")
    )


def _db():
    con = sqlite3.connect(_db_path(), timeout=8.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 8000")
    return con


def _ensure_tables(db_path: Optional[str] = None) -> None:
    con = None
    try:
        con = sqlite3.connect(db_path or _db_path(), timeout=8.0)
        con.execute("PRAGMA busy_timeout = 8000")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS social_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                account_label TEXT,
                account_ref TEXT,
                status TEXT DEFAULT 'connected',
                scopes TEXT,
                connected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                meta TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_social_conn_platform
                ON social_connections(platform);
            """
        )
        con.commit()
    finally:
        if con is not None:
            con.close()


def _secure_write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _token_path(platform: str, account_ref: str) -> str:
    safe = "".join(c for c in (account_ref or "") if c.isalnum() or c in "-_.@")
    return os.path.join(ACCOUNTS_DIR, platform, safe or "default", "token.json")


def _platform_creds_path(platform: str) -> str:
    return os.path.join(CREDS_DIR, f"{platform}.json")


def _google_client_secret_path() -> Optional[str]:
    """Prefer a YouTube-specific client secret; fall back to the email one."""
    p = _platform_creds_path("youtube")
    if os.path.exists(p):
        return p
    if os.path.exists(EMAIL_CREDENTIALS_PATH):
        return EMAIL_CREDENTIALS_PATH
    return None


def _row_to_conn(r: sqlite3.Row) -> dict:
    try:
        meta = json.loads(r["meta"] or "{}")
    except Exception:
        meta = {}
    return {
        "id": r["id"],
        "platform": r["platform"],
        "account_label": r["account_label"],
        "account_ref": r["account_ref"],
        "status": r["status"],
        "scopes": r["scopes"],
        "connected_at": r["connected_at"],
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# Google/YouTube boundary — monkeypatched in tests
# ---------------------------------------------------------------------------
def _yt_build_flow():
    from google_auth_oauthlib.flow import Flow

    secret = _google_client_secret_path()
    if not secret:
        raise RuntimeError(
            "No Google OAuth client configured. Set one in Connections "
            "(or ensure email-pipeline/credentials.json exists)."
        )
    return Flow.from_client_secrets_file(
        secret, scopes=YT_SCOPES, redirect_uri=OAUTH_REDIRECT_URI
    )


def _load_creds(platform: str, account_ref: str):
    from google.oauth2.credentials import Credentials

    path = _token_path(platform, account_ref)
    if not os.path.exists(path):
        raise RuntimeError("token missing — reconnect the account")
    return Credentials.from_authorized_user_file(path, YT_SCOPES)


def _yt_channel_label(creds) -> tuple[str, str]:
    """Return (channel_title, channel_id) for the authorized account."""
    from googleapiclient.discovery import build

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    resp = yt.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        return ("YouTube account", "")
    sn = items[0]
    return (sn.get("snippet", {}).get("title", "YouTube account"), sn.get("id", ""))


def _yt_upload(creds, video_path: str, title: str, description: str,
               tags: list) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {"title": title[:100] or "Untitled",
                    "description": description or "", "tags": tags or []},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video_path, resumable=False)
    resp = yt.videos().insert(part="snippet,status", body=body,
                              media_body=media).execute()
    vid = resp.get("id", "")
    return {"id": vid, "url": f"https://youtu.be/{vid}" if vid else ""}


def _yt_recent_uploads(creds, limit: int = 12) -> list:
    from googleapiclient.discovery import build

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    items = ch.get("items", [])
    if not items:
        return []
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = yt.playlistItems().list(
        part="snippet", playlistId=uploads, maxResults=min(limit, 50)
    ).execute()
    out = []
    for it in pl.get("items", []):
        sn = it.get("snippet", {})
        vid = sn.get("resourceId", {}).get("videoId", "")
        thumbs = sn.get("thumbnails", {})
        thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        out.append({
            "id": vid,
            "title": sn.get("title", ""),
            "published_at": sn.get("publishedAt", ""),
            "thumbnail": thumb,
            "url": f"https://youtu.be/{vid}" if vid else "",
        })
    return out


# ---------------------------------------------------------------------------
# post helpers (read from the social_studio tables)
# ---------------------------------------------------------------------------
def _get_post_row(pid: int) -> Optional[dict]:
    con = _db()
    try:
        r = con.execute(
            "SELECT * FROM ahb_social_posts WHERE id=?", (pid,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def register(bp):
    _ensure_tables()

    @bp.route("/api/ahb/social/connections", methods=["GET"])
    def social_connections_list():
        con = _db()
        try:
            rows = con.execute(
                "SELECT * FROM social_connections ORDER BY connected_at DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            con.close()
        return jsonify({
            "items": [_row_to_conn(r) for r in rows],
            "platforms": list(PLATFORMS),
            "oauth_platforms": list(OAUTH_PLATFORMS),
            "token_platforms": list(TOKEN_PLATFORMS),
            "public_base_set": bool(_public_base()),
        })

    @bp.route("/api/ahb/social/connections/app-creds", methods=["GET"])
    def social_appcreds_status():
        out = {}
        for p in PLATFORMS:
            configured = os.path.exists(_platform_creds_path(p))
            if p == "youtube" and not configured:
                configured = os.path.exists(EMAIL_CREDENTIALS_PATH)
            out[p] = configured
        return jsonify({"configured": out})

    @bp.route("/api/ahb/social/connections/app-creds", methods=["PUT"])
    def social_appcreds_set():
        data = request.get_json(silent=True) or {}
        platform = (data.get("platform") or "").strip().lower()
        client_json = data.get("client_json")
        if platform not in PLATFORMS:
            return jsonify({"error": f"unknown platform: {platform}"}), 400
        if not client_json:
            return jsonify({"error": "client_json required"}), 400
        # Accept either a JSON string or an object.
        if isinstance(client_json, str):
            try:
                parsed = json.loads(client_json)
            except json.JSONDecodeError:
                return jsonify({"error": "client_json is not valid JSON"}), 400
        else:
            parsed = client_json
        _secure_write(_platform_creds_path(platform), json.dumps(parsed))
        return jsonify({"ok": True, "platform": platform})

    # ---- YouTube OAuth (paste-back, mirrors email studio) -----------------
    @bp.route("/api/ahb/social/connections/<platform>/auth/start",
              methods=["POST"])
    def social_auth_start(platform):
        platform = (platform or "").lower()
        if platform not in OAUTH_PLATFORMS:
            return jsonify({
                "ok": False,
                "error": f"{platform} OAuth is not available yet (Phase 2/3). "
                         f"Use Manual export for now.",
            }), 400
        try:
            flow = _yt_build_flow()
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        auth_url, state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )
        flow_id = _flow_id()
        cutoff = time.time() - 3600
        for fid in [f for f, v in _oauth_flows.items()
                    if v.get("created", 0) < cutoff]:
            _oauth_flows.pop(fid, None)
        _oauth_flows[flow_id] = {
            "status": "pending", "flow": flow, "platform": platform,
            "state": state, "created": time.time(),
        }
        return jsonify({"ok": True, "flow_id": flow_id, "auth_url": auth_url,
                        "redirect_uri": OAUTH_REDIRECT_URI})

    @bp.route("/api/ahb/social/connections/<platform>/auth/finish",
              methods=["POST"])
    def social_auth_finish(platform):
        data = request.get_json(silent=True) or {}
        flow_id = (data.get("flow_id") or "").strip()
        raw = (data.get("redirect_url") or "").strip()
        if not flow_id or not raw:
            return jsonify({"ok": False,
                            "error": "missing flow_id or redirect_url"}), 400
        code = raw
        if "://" in raw or "?" in raw:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(raw).query)
            if qs.get("error"):
                return jsonify({"ok": False, "error": qs["error"][0]}), 400
            code = (qs.get("code") or [""])[0]
        if not code:
            return jsonify({"ok": False,
                            "error": "no authorization code found"}), 400
        result = _finish_oauth(flow_id, code)
        if result["status"] == "done":
            return jsonify({"ok": True, **result})
        return jsonify({"ok": False,
                        "error": result.get("error", "unknown")}), 400

    @bp.route("/api/ahb/social/connections/oauth/callback", methods=["GET"])
    def social_oauth_callback():
        state = request.args.get("state", "")
        code = request.args.get("code", "")
        error = request.args.get("error", "")
        flow_id = next(
            (f for f, v in _oauth_flows.items() if v.get("state") == state), None
        )

        def _page(title, body):
            return (f"<html><body style='font-family:sans-serif;background:#0a0a1a;"
                    f"color:#e0e0e0;padding:40px'><h2>{title}</h2><p>{body}</p>"
                    f"</body></html>"), 200

        if not flow_id:
            return _page("⚠ Unknown OAuth flow",
                         "This flow expired. Start again from the Social tab.")
        if error:
            _oauth_flows[flow_id].update({"status": "failed", "error": error})
            return _page("⚠ Sign-in failed", error)
        if not code:
            return _page("⚠ Missing authorization code", "Try again.")
        result = _finish_oauth(flow_id, code)
        if result["status"] == "done":
            return _page("✅ Account connected",
                         f"<strong>{result.get('account_label')}</strong> is "
                         f"connected. Return to the Social tab.")
        return _page("⚠ Could not connect", result.get("error", "unknown error"))

    @bp.route("/api/ahb/social/connections/<int:cid>", methods=["DELETE"])
    def social_conn_delete(cid):
        con = _db()
        try:
            r = con.execute(
                "SELECT platform, account_ref FROM social_connections WHERE id=?",
                (cid,),
            ).fetchone()
            if not r:
                return jsonify({"error": "not found"}), 404
            con.execute("DELETE FROM social_connections WHERE id=?", (cid,))
            con.commit()
        finally:
            con.close()
        tp = _token_path(r["platform"], r["account_ref"] or "")
        try:
            if os.path.exists(tp):
                os.remove(tp)
        except OSError:
            pass
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/connections/<int:cid>/feed", methods=["GET"])
    def social_conn_feed(cid):
        con = _db()
        try:
            r = con.execute(
                "SELECT * FROM social_connections WHERE id=?", (cid,)
            ).fetchone()
        finally:
            con.close()
        if not r:
            return jsonify({"error": "not found"}), 404
        platform = r["platform"]
        try:
            limit = min(max(int(request.args.get("limit", 12)), 1), 50)
        except (TypeError, ValueError):
            limit = 12
        meta = _get_conn_meta(r)
        try:
            if platform == "youtube":
                creds = _load_creds("youtube", r["account_ref"] or "")
                items = _yt_recent_uploads(creds, limit)
            elif platform == "facebook":
                token = _meta_token("facebook", r["account_ref"] or "")
                items = _meta_page_feed(meta.get("page_id") or r["account_ref"],
                                        token, limit)
            elif platform == "instagram":
                token = _meta_token("instagram", r["account_ref"] or "")
                items = _meta_ig_media(meta.get("ig_user_id") or r["account_ref"],
                                       token, limit)
            else:  # tiktok
                items = _tt_video_list(_tt_token(r["account_ref"] or ""), limit)
        except Exception as e:
            return jsonify({"error": str(e)}), 502
        return jsonify({"items": items})

    # ---- publish + manual export ------------------------------------------
    @bp.route("/api/ahb/social/posts/<int:pid>/publish", methods=["POST"])
    def social_post_publish(pid):
        data = request.get_json(silent=True) or {}
        if not data.get("confirm"):
            return jsonify({"error": "confirm required — publishing is "
                                     "outward-facing"}), 400
        conn_id = data.get("connection_id")
        if not conn_id:
            return jsonify({"error": "connection_id required"}), 400
        post = _get_post_row(pid)
        if not post:
            return jsonify({"error": "post not found"}), 404
        con = _db()
        try:
            r = con.execute(
                "SELECT * FROM social_connections WHERE id=?", (conn_id,)
            ).fetchone()
        finally:
            con.close()
        if not r:
            return jsonify({"error": "connection not found"}), 404
        platform = r["platform"]
        if platform in ("facebook", "instagram"):
            body, code = _publish_meta(platform, r, post, pid)
            return jsonify(body), code
        if platform == "tiktok":
            body, code = _publish_tiktok(r, post, pid, data)
            return jsonify(body), code
        if platform != "youtube":
            return jsonify({
                "error": f"Direct publish to {platform} is not supported.",
                "manual_export": f"/api/ahb/social/posts/{pid}/manual-export",
            }), 501
        asset = post.get("asset_path")
        if not asset or not os.path.exists(asset) or \
                os.path.splitext(asset)[1].lower() not in _VIDEO_EXTS:
            return jsonify({
                "error": "post has no rendered video asset — render the post "
                         "first (YouTube uploads require a video).",
            }), 400
        caption = post.get("caption") or ""
        hashtags = post.get("hashtags") or ""
        tags = [t.lstrip("#") for t in hashtags.split() if t.strip()]
        try:
            creds = _load_creds("youtube", r["account_ref"] or "")
            res = _yt_upload(creds, asset, caption[:100] or "AHB Short",
                             (caption + "\n\n" + hashtags).strip(), tags)
        except Exception as e:
            return jsonify({"error": str(e)}), 502
        url = res.get("url", "")
        con = _db()
        try:
            con.execute(
                "UPDATE ahb_social_posts SET status='posted', posted_url=?, "
                "posted_at=CURRENT_TIMESTAMP WHERE id=?", (url, pid),
            )
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True, "url": url, "platform": "youtube"})

    @bp.route("/api/ahb/social/posts/<int:pid>/manual-export", methods=["GET"])
    def social_post_manual_export(pid):
        post = _get_post_row(pid)
        if not post:
            return jsonify({"error": "post not found"}), 404
        asset = post.get("asset_path")
        has_asset = bool(asset and os.path.exists(asset))
        return jsonify({
            "caption": post.get("caption") or "",
            "hashtags": post.get("hashtags") or "",
            "first_comment": post.get("first_comment") or "",
            "platform": post.get("platform"),
            "has_asset": has_asset,
            "asset_filename": os.path.basename(asset) if has_asset else None,
            # Download links use existing serving routes.
            "bundle_url": f"/api/ahb/social/posts/{pid}/bundle",
            "cover_url": f"/api/ahb/social/posts/{pid}/cover",
        })

    # ---- public asset serve (Meta pulls media from a public URL) ----------
    @bp.route("/api/ahb/social/posts/<int:pid>/asset", methods=["GET"])
    def social_post_asset(pid):
        post = _get_post_row(pid)
        if not post:
            return jsonify({"error": "not found"}), 404
        asset = post.get("asset_path")
        if not asset or not os.path.exists(asset):
            return jsonify({"error": "no asset"}), 404
        return send_file(asset)

    # ---- Meta (Instagram + Facebook) connect via pasted token -------------
    @bp.route("/api/ahb/social/connections/meta/token", methods=["POST"])
    def social_meta_token():
        data = request.get_json(silent=True) or {}
        token = (data.get("access_token") or "").strip()
        if not token:
            return jsonify({"error": "access_token required"}), 400
        try:
            pages = _meta_list_pages(token)
        except Exception as e:
            return jsonify({"error": f"could not list pages: {e}"}), 502
        ref = _flow_id()
        cutoff = time.time() - 3600
        for k in [k for k, v in _meta_sessions.items()
                  if v.get("created", 0) < cutoff]:
            _meta_sessions.pop(k, None)
        _meta_sessions[ref] = {"pages": pages, "created": time.time()}
        safe = [{
            "id": p.get("id"), "name": p.get("name"),
            "has_instagram": bool(p.get("instagram_business_account")),
            "ig_username": (p.get("instagram_business_account") or {}).get("username", ""),
        } for p in pages]
        return jsonify({"ok": True, "ref": ref, "pages": safe})

    @bp.route("/api/ahb/social/connections/meta/add", methods=["POST"])
    def social_meta_add():
        data = request.get_json(silent=True) or {}
        ref = (data.get("ref") or "").strip()
        page_id = (data.get("page_id") or "").strip()
        connect_ig = bool(data.get("connect_instagram"))
        sess = _meta_sessions.get(ref)
        if not sess:
            return jsonify({"error": "session expired — paste the token again"}), 400
        page = next((p for p in sess["pages"] if p.get("id") == page_id), None)
        if not page:
            return jsonify({"error": "page not found in session"}), 404
        page_token = page.get("access_token")
        if not page_token:
            return jsonify({"error": "no page access token returned by Meta"}), 502
        created = []
        _secure_write(_token_path("facebook", page_id),
                      json.dumps({"page_token": page_token}))
        fb_id = _upsert_connection("facebook", page.get("name") or page_id,
                                   page_id, "pages_manage_posts")
        _set_conn_meta(fb_id, {"page_id": page_id})
        created.append({"platform": "facebook", "id": fb_id})
        iga = page.get("instagram_business_account") or {}
        if connect_ig and iga.get("id"):
            ig_ref = iga["id"]
            _secure_write(_token_path("instagram", ig_ref),
                          json.dumps({"page_token": page_token}))
            ig_id = _upsert_connection("instagram", iga.get("username") or ig_ref,
                                       ig_ref, "instagram_content_publish")
            _set_conn_meta(ig_id, {"ig_user_id": ig_ref, "page_id": page_id})
            created.append({"platform": "instagram", "id": ig_id})
        _meta_sessions.pop(ref, None)
        return jsonify({"ok": True, "created": created})

    # ---- TikTok connect via pasted access token ---------------------------
    @bp.route("/api/ahb/social/connections/tiktok/token", methods=["POST"])
    def social_tiktok_token():
        data = request.get_json(silent=True) or {}
        token = (data.get("access_token") or "").strip()
        open_id = (data.get("open_id") or "").strip()
        if not token:
            return jsonify({"error": "access_token required"}), 400
        try:
            info = _tt_creator_info(token)
        except Exception as e:
            return jsonify({"error": f"could not validate token: {e}"}), 502
        nickname = info.get("creator_nickname") or "TikTok account"
        ref = open_id or info.get("creator_username") or nickname or "default"
        _secure_write(_token_path("tiktok", ref),
                      json.dumps({"access_token": token, "open_id": open_id}))
        privacy_opts = info.get("privacy_level_options") or []
        cid = _upsert_connection("tiktok", nickname, ref, "video.publish")
        _set_conn_meta(cid, {"open_id": open_id, "privacy_options": privacy_opts})
        return jsonify({"ok": True, "connection_id": cid,
                        "account_label": nickname,
                        "privacy_options": privacy_opts})


def _finish_oauth(flow_id: str, code: str) -> dict:
    entry = _oauth_flows.get(flow_id)
    if not entry or "flow" not in entry:
        return {"status": "failed", "error": "unknown or expired flow"}
    try:
        flow = entry["flow"]
        flow.fetch_token(code=code)
        creds = flow.credentials
        platform = entry.get("platform", "youtube")
        label, ref = _yt_channel_label(creds)
        if not ref:
            ref = label or "default"
        _secure_write(_token_path(platform, ref), creds.to_json())
        cid = _upsert_connection(platform, label, ref, " ".join(YT_SCOPES))
        result = {"status": "done", "connection_id": cid,
                  "account_label": label, "platform": platform}
    except Exception as e:
        result = {"status": "failed", "error": str(e)}
    entry.update({k: v for k, v in result.items() if k != "flow"})
    return result


def _upsert_connection(platform: str, label: str, ref: str, scopes: str) -> int:
    con = _db()
    try:
        existing = con.execute(
            "SELECT id FROM social_connections WHERE platform=? AND account_ref=?",
            (platform, ref),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE social_connections SET account_label=?, status='connected', "
                "scopes=?, connected_at=CURRENT_TIMESTAMP WHERE id=?",
                (label, scopes, existing["id"]),
            )
            con.commit()
            return existing["id"]
        cur = con.execute(
            "INSERT INTO social_connections (platform, account_label, account_ref, "
            "status, scopes) VALUES (?, ?, ?, 'connected', ?)",
            (platform, label, ref, scopes),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _flow_id() -> str:
    import secrets as _s
    return _s.token_urlsafe(16)


def _set_conn_meta(cid: int, meta: dict) -> None:
    con = _db()
    try:
        con.execute("UPDATE social_connections SET meta=? WHERE id=?",
                    (json.dumps(meta), cid))
        con.commit()
    finally:
        con.close()


def _get_conn_meta(row) -> dict:
    try:
        return json.loads(row["meta"] or "{}")
    except Exception:
        return {}


def _meta_token(platform: str, account_ref: str) -> str:
    """Return the stored Page access token for a Meta connection."""
    p = _token_path(platform, account_ref)
    if not os.path.exists(p):
        raise RuntimeError("token missing — reconnect the account")
    with open(p) as f:
        return (json.load(f) or {}).get("page_token", "")


def _mark_posted(pid: int, url: str) -> None:
    con = _db()
    try:
        con.execute(
            "UPDATE ahb_social_posts SET status='posted', posted_url=?, "
            "posted_at=CURRENT_TIMESTAMP WHERE id=?", (url, pid),
        )
        con.commit()
    finally:
        con.close()


def _publish_meta(platform: str, conn_row, post: dict, pid: int) -> tuple:
    """Publish a post to Facebook/Instagram. Returns (body_dict, status_code)."""
    asset = post.get("asset_path")
    cover = post.get("cover_path")
    caption = post.get("caption") or ""
    hashtags = post.get("hashtags") or ""
    full = (caption + ("\n\n" + hashtags if hashtags else "")).strip()
    is_video = bool(asset and os.path.exists(asset)
                    and os.path.splitext(asset)[1].lower() in _VIDEO_EXTS)
    has_image = bool((cover and os.path.exists(cover))
                     or (asset and not is_video and os.path.exists(asset)))
    if not is_video and not has_image:
        return {"error": "post has no rendered asset — render it first."}, 400
    if not _public_base():
        return {
            "error": "set SOCIAL_PUBLIC_BASE_URL (the public https origin that "
                     "fronts baza) so the platform can fetch the media, or use "
                     "Manual export.",
            "manual_export": f"/api/ahb/social/posts/{pid}/manual-export",
        }, 400
    media_url = _public_asset_url(pid, "video" if is_video else "image")
    meta = _get_conn_meta(conn_row)
    try:
        token = _meta_token(platform, conn_row["account_ref"] or "")
        if platform == "facebook":
            page_id = meta.get("page_id") or conn_row["account_ref"]
            res = (_meta_publish_video(page_id, token, media_url, full) if is_video
                   else _meta_publish_photo(page_id, token, media_url, full))
        else:  # instagram
            ig_id = meta.get("ig_user_id") or conn_row["account_ref"]
            res = _meta_ig_publish(ig_id, token, media_url, full, is_video)
    except Exception as e:
        return {"error": str(e)}, 502
    url = res.get("url", "")
    _mark_posted(pid, url)
    return {"ok": True, "url": url, "platform": platform}, 200


def _tt_token(account_ref: str) -> str:
    p = _token_path("tiktok", account_ref)
    if not os.path.exists(p):
        raise RuntimeError("token missing — reconnect the account")
    with open(p) as f:
        return (json.load(f) or {}).get("access_token", "")


def _publish_tiktok(conn_row, post: dict, pid: int, data: dict) -> tuple:
    """Publish a post to TikTok. Returns (body_dict, status_code)."""
    asset = post.get("asset_path")
    is_video = bool(asset and os.path.exists(asset)
                    and os.path.splitext(asset)[1].lower() in _VIDEO_EXTS)
    if not is_video:
        return {"error": "TikTok needs a rendered video asset — render the "
                         "post first."}, 400
    if not _public_base():
        return {
            "error": "set SOCIAL_PUBLIC_BASE_URL (a public https origin fronting "
                     "baza, also added as a verified URL prefix in your TikTok "
                     "app) so TikTok can pull the video, or use Manual export.",
            "manual_export": f"/api/ahb/social/posts/{pid}/manual-export",
        }, 400
    meta = _get_conn_meta(conn_row)
    opts = meta.get("privacy_options") or []
    privacy = (data or {}).get("privacy_level") or (
        "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opts else "SELF_ONLY")
    caption = post.get("caption") or ""
    hashtags = post.get("hashtags") or ""
    title = (caption + (" " + hashtags if hashtags else "")).strip()[:150]
    video_url = _public_asset_url(pid, "video")
    try:
        token = _tt_token(conn_row["account_ref"] or "")
        res = _tt_publish_video(token, video_url, title, privacy)
    except Exception as e:
        return {"error": str(e)}, 502
    note = ("Uploaded to TikTok as a private draft (SELF_ONLY) — open the TikTok "
            "app to review and post publicly. Public direct-post requires your "
            "TikTok app to be audited.") if privacy == "SELF_ONLY" \
        else "Published to TikTok."
    _mark_posted(pid, "")
    return {"ok": True, "publish_id": res.get("publish_id", ""),
            "platform": "tiktok", "privacy_level": privacy, "note": note}, 200
