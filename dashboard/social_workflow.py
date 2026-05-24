"""Social Studio v2.2 — templates, tags, bulk ops, versions, approval log."""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import tempfile
import urllib.request
import zipfile
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")

_ALLOWED_STATUSES = {
    "draft", "pending_review", "approved", "scheduled",
    "posted", "rejected", "failed",
}

_BULK_ACTIONS = {
    "set_status", "schedule", "delete", "tag", "telegram", "bundle",
}


def _db():
    path = os.environ.get("BAZA_DASHBOARD_DB",
                          os.path.join(_HERE, "baza_projects.db"))
    con = sqlite3.connect(path, timeout=8.0)
    con.row_factory = sqlite3.Row
    return con


TEMPLATE_WRITABLE = {
    "name", "caption_template", "hashtag_set", "platform_targets",
    "first_comment_template", "music_id", "voiceover_script",
}


def _row_to_template(r):
    d = dict(r)
    try:
        d["platform_targets"] = json.loads(d["platform_targets"]) if d.get("platform_targets") else []
    except Exception:
        d["platform_targets"] = []
    return d


def _interpolate(template: str, variables: dict) -> str:
    if not template:
        return ""
    def repl(m):
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", repl, template)


def register(bp):
    from flask import jsonify, request

    @bp.route("/api/ahb/social/templates", methods=["GET"])
    def social_templates_list():
        con = _db()
        try:
            rows = con.execute("SELECT * FROM ahb_social_post_templates ORDER BY id DESC").fetchall()
        finally:
            con.close()
        return jsonify({"items": [_row_to_template(r) for r in rows]})

    @bp.route("/api/ahb/social/templates", methods=["POST"])
    def social_templates_create():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        cols, vals = ["name"], [name]
        for k, v in data.items():
            if k == "name" or k not in TEMPLATE_WRITABLE:
                continue
            cols.append(k)
            vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        con = _db()
        try:
            cur = con.execute(
                f"INSERT INTO ahb_social_post_templates ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                vals,
            )
            con.commit()
            tid = cur.lastrowid
        finally:
            con.close()
        return jsonify({"id": tid})

    @bp.route("/api/ahb/social/templates/<int:tid>", methods=["PUT"])
    def social_templates_update(tid: int):
        data = request.get_json(silent=True) or {}
        sets, vals = [], []
        for k, v in data.items():
            if k not in TEMPLATE_WRITABLE:
                continue
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        if not sets:
            return jsonify({"error": "no writable fields"}), 400
        sets.append("updated_at=?"); vals.append(datetime.utcnow().isoformat(timespec="seconds"))
        vals.append(tid)
        con = _db()
        try:
            con.execute(f"UPDATE ahb_social_post_templates SET {','.join(sets)} WHERE id=?", vals)
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/templates/<int:tid>", methods=["DELETE"])
    def social_templates_delete(tid: int):
        con = _db()
        try:
            cur = con.execute("DELETE FROM ahb_social_post_templates WHERE id=?", (tid,))
            con.commit()
            deleted = cur.rowcount
        finally:
            con.close()
        if deleted == 0:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/templates/<int:tid>/apply", methods=["POST"])
    def social_templates_apply(tid: int):
        data = request.get_json(silent=True) or {}
        variables = data.get("variables") or {}
        con = _db()
        try:
            r = con.execute("SELECT * FROM ahb_social_post_templates WHERE id=?", (tid,)).fetchone()
        finally:
            con.close()
        if not r:
            return jsonify({"error": "not found"}), 404
        t = _row_to_template(r)
        from datetime import date
        variables.setdefault("date", date.today().isoformat())
        return jsonify({
            "caption": _interpolate(t["caption_template"] or "", variables),
            "hashtags": t["hashtag_set"] or "",
            "first_comment": _interpolate(t.get("first_comment_template") or "", variables),
            "platform_targets": t["platform_targets"],
            "music_id": t.get("music_id"),
            "voiceover_script": t.get("voiceover_script"),
            "template_id": tid,
        })

    # ---- Tags / collections ----------------------------------------------

    @bp.route("/api/ahb/social/tags", methods=["GET"])
    def social_tags_list():
        con = _db()
        try:
            rows = con.execute(
                "SELECT id, name, color, created_at FROM ahb_social_tags ORDER BY name COLLATE NOCASE"
            ).fetchall()
        finally:
            con.close()
        return jsonify({"items": [dict(r) for r in rows]})

    @bp.route("/api/ahb/social/tags", methods=["POST"])
    def social_tags_create():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        color = (data.get("color") or "#10b981").strip() or "#10b981"
        if not _COLOR_RE.match(color):
            return jsonify({"error": "invalid color (expected #rgb / #rrggbb / #rrggbbaa)"}), 400
        con = _db()
        try:
            try:
                cur = con.execute(
                    "INSERT INTO ahb_social_tags (name, color) VALUES (?, ?)",
                    (name, color),
                )
                con.commit()
                tid = cur.lastrowid
            except sqlite3.IntegrityError:
                return jsonify({"error": "tag name already exists"}), 409
        finally:
            con.close()
        return jsonify({"id": tid, "name": name, "color": color})

    @bp.route("/api/ahb/social/tags/<int:tid>", methods=["PUT"])
    def social_tags_update(tid: int):
        data = request.get_json(silent=True) or {}
        sets, vals = [], []
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "name cannot be empty"}), 400
            sets.append("name=?"); vals.append(name)
        if "color" in data:
            color = (data.get("color") or "#10b981").strip() or "#10b981"
            if not _COLOR_RE.match(color):
                return jsonify({"error": "invalid color"}), 400
            sets.append("color=?"); vals.append(color)
        if not sets:
            return jsonify({"error": "no writable fields"}), 400
        vals.append(tid)
        con = _db()
        try:
            # 404 if not found
            row = con.execute("SELECT id FROM ahb_social_tags WHERE id=?", (tid,)).fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
            try:
                con.execute(f"UPDATE ahb_social_tags SET {','.join(sets)} WHERE id=?", vals)
                con.commit()
            except sqlite3.IntegrityError:
                return jsonify({"error": "tag name already exists"}), 409
        finally:
            con.close()
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/tags/<int:tid>", methods=["DELETE"])
    def social_tags_delete(tid: int):
        con = _db()
        try:
            row = con.execute("SELECT id FROM ahb_social_tags WHERE id=?", (tid,)).fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
            # cascade: post_tags first, then tag itself
            con.execute("DELETE FROM ahb_social_post_tags WHERE tag_id=?", (tid,))
            con.execute("DELETE FROM ahb_social_tags WHERE id=?", (tid,))
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    @bp.route("/api/ahb/social/posts/<int:pid>/tags", methods=["GET"])
    def social_post_tags_get(pid: int):
        con = _db()
        try:
            rows = con.execute(
                "SELECT t.id, t.name, t.color FROM ahb_social_tags t "
                "JOIN ahb_social_post_tags pt ON pt.tag_id = t.id "
                "WHERE pt.post_id=? ORDER BY t.name COLLATE NOCASE",
                (pid,),
            ).fetchall()
        finally:
            con.close()
        return jsonify({"tags": [dict(r) for r in rows]})

    @bp.route("/api/ahb/social/posts/<int:pid>/tags", methods=["POST"])
    def social_post_tags_set(pid: int):
        data = request.get_json(silent=True) or {}
        tag_ids = data.get("tag_ids")
        if not isinstance(tag_ids, list):
            return jsonify({"error": "tag_ids must be a list"}), 400
        # coerce + dedupe + drop non-ints
        clean = []
        seen = set()
        for v in tag_ids:
            try:
                i = int(v)
            except (TypeError, ValueError):
                continue
            if i not in seen:
                seen.add(i)
                clean.append(i)
        con = _db()
        try:
            con.execute("DELETE FROM ahb_social_post_tags WHERE post_id=?", (pid,))
            if clean:
                ph = ",".join("?" * len(clean))
                valid = {r[0] for r in con.execute(
                    f"SELECT id FROM ahb_social_tags WHERE id IN ({ph})",
                    clean,
                ).fetchall()}
            else:
                valid = set()
            applied = 0
            for tid in clean:
                if tid not in valid:
                    continue
                con.execute(
                    "INSERT OR IGNORE INTO ahb_social_post_tags (post_id, tag_id) VALUES (?, ?)",
                    (pid, tid),
                )
                applied += 1
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True, "applied": applied})

    # ---- T5: bulk operations ---------------------------------------------

    @bp.route("/api/ahb/social/posts/bulk", methods=["POST"])
    def social_posts_bulk():
        from flask import send_file, after_this_request

        data = request.get_json(silent=True) or {}
        raw_ids = data.get("ids")
        action = (data.get("action") or "").strip()
        params = data.get("params") or {}

        if not isinstance(raw_ids, list) or not raw_ids:
            return jsonify({"error": "ids must be a non-empty list"}), 400
        # coerce ids → int; reject non-ints (don't silently drop)
        ids = []
        for v in raw_ids:
            if isinstance(v, bool):
                return jsonify({"error": "ids must be integers"}), 400
            try:
                ids.append(int(v))
            except (TypeError, ValueError):
                return jsonify({"error": "ids must be integers"}), 400
        # dedupe while preserving order
        seen = set()
        unique_ids = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                unique_ids.append(i)
        ids = unique_ids

        if action not in _BULK_ACTIONS:
            return jsonify({"error": f"unknown action: {action}"}), 400

        ph = ",".join("?" * len(ids))

        # ---- set_status ---------------------------------------------------
        if action == "set_status":
            status = (params.get("status") or "").strip()
            if status not in _ALLOWED_STATUSES:
                return jsonify({"error": f"invalid status: {status}"}), 400
            con = _db()
            try:
                cur = con.execute(
                    f"UPDATE ahb_social_posts SET status=?, "
                    f"updated_at=? WHERE id IN ({ph})",
                    [status, datetime.utcnow().isoformat(timespec="seconds"), *ids],
                )
                con.commit()
                affected = cur.rowcount
            finally:
                con.close()
            return jsonify({"ok": True, "action": action, "affected": affected})

        # ---- schedule -----------------------------------------------------
        if action == "schedule":
            sched = (params.get("scheduled_at") or "").strip()
            if not sched:
                return jsonify({"error": "scheduled_at required"}), 400
            con = _db()
            try:
                cur = con.execute(
                    f"UPDATE ahb_social_posts SET scheduled_at=?, "
                    f"status='scheduled', updated_at=? WHERE id IN ({ph})",
                    [sched, datetime.utcnow().isoformat(timespec="seconds"), *ids],
                )
                con.commit()
                affected = cur.rowcount
            finally:
                con.close()
            return jsonify({"ok": True, "action": action, "affected": affected})

        # ---- delete -------------------------------------------------------
        if action == "delete":
            con = _db()
            try:
                # cascade: drop post_tags first
                con.execute(
                    f"DELETE FROM ahb_social_post_tags WHERE post_id IN ({ph})",
                    ids,
                )
                cur = con.execute(
                    f"DELETE FROM ahb_social_posts WHERE id IN ({ph})",
                    ids,
                )
                con.commit()
                affected = cur.rowcount
            finally:
                con.close()
            return jsonify({"ok": True, "action": action, "affected": affected})

        # ---- tag (replace-set) -------------------------------------------
        if action == "tag":
            tag_ids_raw = params.get("tag_ids")
            if not isinstance(tag_ids_raw, list):
                return jsonify({"error": "params.tag_ids must be a list"}), 400
            clean_tags = []
            seen_t = set()
            for v in tag_ids_raw:
                try:
                    i = int(v)
                except (TypeError, ValueError):
                    continue
                if i not in seen_t:
                    seen_t.add(i)
                    clean_tags.append(i)
            con = _db()
            try:
                # filter to existing tag ids
                if clean_tags:
                    tph = ",".join("?" * len(clean_tags))
                    valid_tags = {
                        r[0] for r in con.execute(
                            f"SELECT id FROM ahb_social_tags WHERE id IN ({tph})",
                            clean_tags,
                        ).fetchall()
                    }
                else:
                    valid_tags = set()
                # only operate on existing posts
                existing = [
                    r[0] for r in con.execute(
                        f"SELECT id FROM ahb_social_posts WHERE id IN ({ph})",
                        ids,
                    ).fetchall()
                ]
                affected = 0
                for pid in existing:
                    # wipe prior tag set
                    con.execute(
                        "DELETE FROM ahb_social_post_tags WHERE post_id=?",
                        (pid,),
                    )
                    for tid in clean_tags:
                        if tid not in valid_tags:
                            continue
                        con.execute(
                            "INSERT OR IGNORE INTO ahb_social_post_tags "
                            "(post_id, tag_id) VALUES (?, ?)",
                            (pid, tid),
                        )
                    affected += 1
                con.commit()
            finally:
                con.close()
            return jsonify({"ok": True, "action": action, "affected": affected})

        # ---- telegram (per-post send) ------------------------------------
        if action == "telegram":
            bridge = os.environ.get("BAZA_SPECTER_BRIDGE", "http://127.0.0.1:8765")
            con = _db()
            try:
                rows = con.execute(
                    f"SELECT * FROM ahb_social_posts WHERE id IN ({ph})",
                    ids,
                ).fetchall()
            finally:
                con.close()
            sent = 0
            failed = 0
            for r in rows:
                d = dict(r)
                payload = {
                    "kind": "social_draft",
                    "post_id": d.get("id"),
                    "platform": d.get("platform"),
                    "caption": d.get("caption") or "",
                    "hashtags": d.get("hashtags") or "",
                    "cover_path": d.get("cover_path"),
                    "asset_path": d.get("asset_path"),
                    "score": d.get("score"),
                    "status": d.get("status"),
                }
                try:
                    req = urllib.request.Request(
                        f"{bridge}/notify",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        if resp.status == 200:
                            sent += 1
                        else:
                            failed += 1
                except Exception:
                    failed += 1
            return jsonify({
                "ok": True, "action": action,
                "affected": sent, "sent": sent, "failed": failed,
            })

        # ---- bundle (zip of per-post sub-dirs) ---------------------------
        if action == "bundle":
            con = _db()
            try:
                rows = con.execute(
                    f"SELECT * FROM ahb_social_posts WHERE id IN ({ph})",
                    ids,
                ).fetchall()
            finally:
                con.close()
            if not rows:
                return jsonify({"error": "no matching posts"}), 404
            tmp = tempfile.NamedTemporaryFile(
                suffix=".zip", prefix="social-bulk-", delete=False,
            )
            tmp.close()
            tmp_path = tmp.name
            included = 0
            try:
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z:
                    for r in rows:
                        d = dict(r)
                        pid = d["id"]
                        sub = f"post-{pid}"
                        asset = d.get("asset_path")
                        if asset and os.path.exists(asset):
                            z.write(asset, arcname=f"{sub}/{os.path.basename(asset)}")
                        cover = d.get("cover_path")
                        if cover and os.path.exists(cover) and cover != asset:
                            z.write(cover, arcname=f"{sub}/cover.jpg")
                        caption_block = (d.get("caption") or "") + "\n\n" + (d.get("hashtags") or "")
                        first_comment = d.get("first_comment")
                        if first_comment:
                            caption_block += "\n\n---\n" + first_comment
                        plat = d.get("platform") or "post"
                        z.writestr(f"{sub}/caption_{plat}.txt", caption_block)
                        # per-language translations if present
                        translations_raw = d.get("translations") or "{}"
                        try:
                            tr = json.loads(translations_raw) if isinstance(translations_raw, str) else translations_raw
                        except Exception:
                            tr = {}
                        if isinstance(tr, dict):
                            for lang, payload in tr.items():
                                if not isinstance(payload, dict):
                                    continue
                                block = (payload.get("caption") or "") + "\n\n" + (payload.get("hashtags") or "")
                                z.writestr(f"{sub}/caption_{plat}.{lang}.txt", block)
                        z.writestr(
                            f"{sub}/manifest.json",
                            json.dumps(d, default=str, indent=2),
                        )
                        included += 1
            except Exception as e:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return jsonify({"error": f"bundle build failed: {e}"}), 500

            @after_this_request
            def _cleanup(response):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return response

            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            return send_file(
                tmp_path,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"social-bulk-bundle-{ts}.zip",
            )

        # Should never reach (action validated above)
        return jsonify({"error": "unhandled action"}), 400
