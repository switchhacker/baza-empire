"""Social Studio v2.2 — stats CRUD, summary, heatmap, hashtag perf, CSV import."""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_ARCHIVE_ROOT = os.path.join(_HERE, "artifacts", "social", "archive")

_DASHBOARD_DIR = _HERE
_ALLOWED_FS_ROOTS_DEFAULT = [
    os.path.join(_DASHBOARD_DIR, "artifacts"),
    os.path.join(_DASHBOARD_DIR, "uploads"),
    os.path.join(_DASHBOARD_DIR, "static"),
]


def _allowed_fs_roots():
    extra = os.environ.get("BAZA_SOCIAL_FS_ROOTS", "")
    roots = list(_ALLOWED_FS_ROOTS_DEFAULT)
    for r in extra.split(":"):
        r = r.strip()
        if r:
            roots.append(os.path.abspath(r))
    return [os.path.abspath(r) for r in roots]


def _path_is_safe(p: str) -> bool:
    """True if `p` resolves inside one of the allowed FS roots.

    An empty/falsy path returns False — callers should pre-check for
    null/empty before invoking this so a missing path doesn't get
    flagged as "unsafe" (it's just absent).
    """
    if not p:
        return False
    try:
        ap = os.path.abspath(os.path.realpath(p))
    except Exception:
        return False
    for root in _allowed_fs_roots():
        if ap == root or ap.startswith(root + os.sep):
            return True
    return False


_COUNT_FIELDS = ("views", "likes", "comments", "saves", "shares")
_OPTIONAL_FIELDS = ("posted_at", "post_url")
_UPSERT_ALLOWED_COLS = frozenset(list(_COUNT_FIELDS) + list(_OPTIONAL_FIELDS))
_HASHTAG_RE = re.compile(r"#\w+")
_MAX_CSV_BYTES = 1024 * 1024  # 1MB cap


def _db():
    path = os.environ.get(
        "BAZA_DASHBOARD_DB",
        os.path.join(_HERE, "baza_projects.db"),
    )
    con = sqlite3.connect(path, timeout=8.0)
    con.row_factory = sqlite3.Row
    return con


def _defaults_for(pid: int) -> dict:
    return {
        "post_id": pid,
        "views": 0,
        "likes": 0,
        "comments": 0,
        "saves": 0,
        "shares": 0,
        "posted_at": None,
        "post_url": None,
        "updated_at": None,
    }


def _coerce_count(v) -> int:
    """Coerce to non-negative int. Raises ValueError if invalid."""
    if isinstance(v, bool):
        raise ValueError("bool not allowed")
    if isinstance(v, int):
        i = v
    elif isinstance(v, float):
        if not v.is_integer():
            raise ValueError("must be integer")
        i = int(v)
    elif isinstance(v, str):
        s = v.strip()
        if not s:
            raise ValueError("empty")
        # Reject leading + or non-digit chars beyond optional minus
        try:
            i = int(s)
        except ValueError:
            raise ValueError("not numeric")
    else:
        raise ValueError("not numeric")
    if i < 0:
        raise ValueError("negative")
    return i


def _parse_iso(s: str):
    """Return datetime or None if unparseable. Tolerates trailing 'Z'."""
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _parse_window(w: str) -> int | None:
    """Returns number of days, or None for 'all' / unrecognized."""
    if not w or w == "all":
        return None
    m = re.match(r"^(\d+)d$", w.strip(), re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def _upsert_analytics(con, pid: int, fields: dict) -> None:
    """Upsert a row into ahb_social_analytics. Caller owns txn."""
    # Defense-in-depth: reject any column not on the allow-list, since
    # `fields.keys()` flow straight into f-string SQL below.
    for k in fields:
        if k not in _UPSERT_ALLOWED_COLS:
            raise ValueError(f"disallowed analytics column: {k}")
    # Determine if row exists
    existing = con.execute(
        "SELECT post_id FROM ahb_social_analytics WHERE post_id=?",
        (pid,),
    ).fetchone()
    now = datetime.utcnow().isoformat(timespec="seconds")
    if existing:
        sets = []
        vals = []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(v)
        sets.append("updated_at=?")
        vals.append(now)
        vals.append(pid)
        con.execute(
            f"UPDATE ahb_social_analytics SET {','.join(sets)} WHERE post_id=?",
            vals,
        )
    else:
        cols = ["post_id"] + list(fields.keys()) + ["updated_at"]
        vals = [pid] + list(fields.values()) + [now]
        placeholders = ",".join("?" * len(cols))
        con.execute(
            f"INSERT INTO ahb_social_analytics ({','.join(cols)}) "
            f"VALUES ({placeholders})",
            vals,
        )


def _engagement_rate(row) -> float:
    """Per-post engagement rate: (likes+comments+saves+shares)/views. 0 if no views."""
    views = row["views"] or 0
    if views <= 0:
        return 0.0
    interactions = (
        (row["likes"] or 0)
        + (row["comments"] or 0)
        + (row["saves"] or 0)
        + (row["shares"] or 0)
    )
    return interactions / views


def register(bp):
    from flask import jsonify, request

    # ---------------- 1. Per-post stats CRUD ----------------

    @bp.route("/api/ahb/social/posts/<int:pid>/analytics", methods=["GET"])
    def analytics_get(pid: int):
        con = _db()
        try:
            row = con.execute(
                "SELECT post_id, views, likes, comments, saves, shares, "
                "posted_at, post_url, updated_at "
                "FROM ahb_social_analytics WHERE post_id=?",
                (pid,),
            ).fetchone()
        finally:
            con.close()
        if not row:
            return jsonify(_defaults_for(pid))
        return jsonify(dict(row))

    @bp.route("/api/ahb/social/posts/<int:pid>/analytics", methods=["PUT"])
    def analytics_put(pid: int):
        data = request.get_json(silent=True) or {}
        fields: dict = {}
        for k in _COUNT_FIELDS:
            if k in data:
                try:
                    fields[k] = _coerce_count(data[k])
                except ValueError as e:
                    return jsonify({
                        "error": f"{k} must be a non-negative integer ({e})",
                    }), 400
        if "posted_at" in data:
            v = data["posted_at"]
            if v is None or v == "":
                fields["posted_at"] = None
            else:
                if not isinstance(v, str) or _parse_iso(v) is None:
                    return jsonify({"error": "posted_at must be ISO 8601"}), 400
                fields["posted_at"] = v
        if "post_url" in data:
            v = data["post_url"]
            if v is None:
                fields["post_url"] = None
            elif isinstance(v, str):
                fields["post_url"] = v.strip() or None
            else:
                return jsonify({"error": "post_url must be a string"}), 400
        if not fields:
            return jsonify({"error": "no writable fields supplied"}), 400
        con = _db()
        try:
            _upsert_analytics(con, pid, fields)
            con.commit()
        finally:
            con.close()
        return jsonify({"ok": True})

    # ---------------- 2. Summary aggregations ----------------

    @bp.route("/api/ahb/social/analytics/summary", methods=["GET"])
    def analytics_summary():
        window = (request.args.get("window") or "30d").strip()
        days = _parse_window(window)
        # Build time filter on analytics.posted_at (fallback: updated_at if missing)
        params: list = []
        time_where = ""
        if days is not None:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(
                timespec="seconds"
            )
            time_where = (
                " AND COALESCE(a.posted_at, a.updated_at) >= ?"
            )
            params.append(cutoff)

        con = _db()
        try:
            base_sql = (
                "SELECT a.post_id, a.views, a.likes, a.comments, a.saves, "
                "a.shares, a.posted_at, p.platform "
                "FROM ahb_social_analytics a "
                "LEFT JOIN ahb_social_posts p ON p.id = a.post_id "
                "WHERE 1=1" + time_where
            )
            rows = con.execute(base_sql, params).fetchall()
        finally:
            con.close()

        totals = {
            "views": 0, "likes": 0, "comments": 0,
            "saves": 0, "shares": 0, "posts": 0,
        }
        per_platform: dict = {}
        eng_rates: list = []

        for r in rows:
            d = dict(r)
            totals["views"] += d["views"] or 0
            totals["likes"] += d["likes"] or 0
            totals["comments"] += d["comments"] or 0
            totals["saves"] += d["saves"] or 0
            totals["shares"] += d["shares"] or 0
            totals["posts"] += 1
            if (d["views"] or 0) > 0:
                eng_rates.append(_engagement_rate(d))
            platform = d["platform"] or "unknown"
            pp = per_platform.setdefault(platform, {
                "views": 0, "likes": 0, "comments": 0,
                "saves": 0, "shares": 0, "posts": 0,
                "_eng_rates": [],
            })
            pp["views"] += d["views"] or 0
            pp["likes"] += d["likes"] or 0
            pp["comments"] += d["comments"] or 0
            pp["saves"] += d["saves"] or 0
            pp["shares"] += d["shares"] or 0
            pp["posts"] += 1
            if (d["views"] or 0) > 0:
                pp["_eng_rates"].append(_engagement_rate(d))

        avg_engagement = (
            sum(eng_rates) / len(eng_rates) if eng_rates else 0.0
        )
        # Finalize per-platform: avg engagement, drop helper
        by_platform = {}
        for plat, pp in per_platform.items():
            rates = pp.pop("_eng_rates")
            pp["avg_engagement_rate"] = (
                sum(rates) / len(rates) if rates else 0.0
            )
            by_platform[plat] = pp

        return jsonify({
            "window": window,
            "totals": totals,
            "avg_engagement_rate": avg_engagement,
            "by_platform": by_platform,
        })

    # ---------------- 3. Heatmap (7x24 by dow x hour) ----------------

    @bp.route("/api/ahb/social/analytics/heatmap", methods=["GET"])
    def analytics_heatmap():
        # Build empty 7x24 grid: each cell = [engagement_rate, post_count]
        grid = [[[0.0, 0] for _ in range(24)] for _ in range(7)]
        sums = [[0.0 for _ in range(24)] for _ in range(7)]

        con = _db()
        try:
            rows = con.execute(
                "SELECT post_id, views, likes, comments, saves, shares, posted_at "
                "FROM ahb_social_analytics "
                "WHERE posted_at IS NOT NULL AND posted_at != ''"
            ).fetchall()
        finally:
            con.close()

        for r in rows:
            dt = _parse_iso(r["posted_at"] or "")
            if dt is None:
                continue
            # JS-style dow: 0 = Sunday, 1 = Monday, ... 6 = Saturday.
            # Python weekday(): 0 = Mon ... 6 = Sun → (weekday + 1) % 7
            dow = (dt.weekday() + 1) % 7
            hour = dt.hour
            if not (0 <= dow < 7 and 0 <= hour < 24):
                continue
            er = _engagement_rate(r)
            sums[dow][hour] += er
            grid[dow][hour][1] += 1

        cells = []
        for d in range(7):
            row = []
            for h in range(24):
                cnt = grid[d][h][1]
                avg = (sums[d][h] / cnt) if cnt > 0 else 0.0
                row.append([avg, cnt])
            cells.append(row)
        return jsonify({"cells": cells})

    # ---------------- 4. Hashtag perf ----------------

    @bp.route("/api/ahb/social/analytics/hashtags", methods=["GET"])
    def analytics_hashtags():
        con = _db()
        try:
            rows = con.execute(
                "SELECT p.id, p.hashtags, "
                "COALESCE(a.views, 0) AS views, "
                "COALESCE(a.likes, 0) AS likes, "
                "COALESCE(a.comments, 0) AS comments, "
                "COALESCE(a.saves, 0) AS saves, "
                "COALESCE(a.shares, 0) AS shares "
                "FROM ahb_social_posts p "
                "LEFT JOIN ahb_social_analytics a ON a.post_id = p.id"
            ).fetchall()
        finally:
            con.close()

        agg: dict = {}
        for r in rows:
            tags_text = r["hashtags"] or ""
            if not tags_text:
                continue
            tags = _HASHTAG_RE.findall(tags_text)
            if not tags:
                continue
            # Dedupe per post — count each tag once per post
            seen = set()
            for t in tags:
                t_norm = t.lower()
                if t_norm in seen:
                    continue
                seen.add(t_norm)
                bucket = agg.setdefault(t_norm, {
                    "tag": t_norm,
                    "post_count": 0,
                    "total_views": 0,
                    "total_likes": 0,
                    "total_comments": 0,
                    "total_saves": 0,
                    "total_shares": 0,
                    "_eng_rates": [],
                })
                bucket["post_count"] += 1
                bucket["total_views"] += r["views"] or 0
                bucket["total_likes"] += r["likes"] or 0
                bucket["total_comments"] += r["comments"] or 0
                bucket["total_saves"] += r["saves"] or 0
                bucket["total_shares"] += r["shares"] or 0
                if (r["views"] or 0) > 0:
                    bucket["_eng_rates"].append(_engagement_rate(r))

        items = []
        for tag, b in agg.items():
            rates = b.pop("_eng_rates")
            b["avg_engagement_rate"] = (
                sum(rates) / len(rates) if rates else 0.0
            )
            items.append(b)
        items.sort(key=lambda x: x["total_views"], reverse=True)
        return jsonify({"items": items[:50]})

    # ---------------- 5. CSV import ----------------

    @bp.route("/api/ahb/social/analytics/import-csv", methods=["POST"])
    def analytics_import_csv():
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "file field required"}), 400
        raw = f.read(_MAX_CSV_BYTES + 1)
        if len(raw) > _MAX_CSV_BYTES:
            return jsonify({
                "error": f"file exceeds {_MAX_CSV_BYTES} byte cap",
            }), 400
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("latin-1")
            except UnicodeDecodeError:
                return jsonify({"error": "could not decode CSV"}), 400

        try:
            reader = csv.DictReader(io.StringIO(text))
        except Exception as e:
            return jsonify({"error": f"csv parse failed: {e}"}), 400

        inserted = 0
        errors: list = []

        con = _db()
        try:
            # Pre-load existing post ids for validation
            existing = {
                r[0] for r in con.execute(
                    "SELECT id FROM ahb_social_posts"
                ).fetchall()
            }
            for idx, row in enumerate(reader, start=2):  # row 1 = header
                if row is None:
                    continue
                raw_pid = (row.get("post_id") or "").strip()
                if not raw_pid:
                    errors.append(f"row {idx}: missing post_id")
                    continue
                try:
                    pid = int(raw_pid)
                except ValueError:
                    errors.append(f"row {idx}: post_id not numeric")
                    continue
                if pid not in existing:
                    errors.append(f"row {idx}: post_id {pid} not found")
                    continue
                fields: dict = {}
                bad = False
                for k in _COUNT_FIELDS:
                    if k not in row or row[k] is None or row[k] == "":
                        continue
                    try:
                        fields[k] = _coerce_count(row[k])
                    except ValueError as e:
                        errors.append(
                            f"row {idx}: {k} invalid ({e})"
                        )
                        bad = True
                        break
                if bad:
                    continue
                # posted_at
                pa = (row.get("posted_at") or "").strip()
                if pa:
                    if _parse_iso(pa) is None:
                        errors.append(
                            f"row {idx}: posted_at not ISO 8601"
                        )
                        continue
                    fields["posted_at"] = pa
                # post_url
                purl = (row.get("post_url") or "").strip()
                if purl:
                    fields["post_url"] = purl
                if not fields:
                    errors.append(f"row {idx}: no updatable fields")
                    continue
                # Per-row SAVEPOINT so one bad row rolls back individually
                # without aborting the rest of the import.
                try:
                    con.execute("SAVEPOINT csv_row")
                    _upsert_analytics(con, pid, fields)
                    con.execute("RELEASE csv_row")
                    inserted += 1
                except Exception as e:
                    try:
                        con.execute("ROLLBACK TO csv_row")
                        con.execute("RELEASE csv_row")
                    except sqlite3.Error:
                        pass
                    errors.append(f"row {idx}: db error ({e})")
            con.commit()
        finally:
            con.close()

        return jsonify({
            "ok": True,
            "inserted": inserted,
            "errors": errors,
        })

    # ---------------- 6. Library cleanup (T11) ----------------

    def _file_size(path: str | None) -> int:
        if not path:
            return 0
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _coerce_ids(data) -> list[int]:
        """Pull ids list from request body. Returns [] on any malformed input."""
        if not isinstance(data, dict):
            return []
        ids = data.get("ids")
        if not isinstance(ids, list):
            return []
        out: list[int] = []
        for v in ids:
            if isinstance(v, bool):
                continue
            if isinstance(v, int):
                out.append(v)
            elif isinstance(v, str) and v.strip().isdigit():
                out.append(int(v.strip()))
        return out

    @bp.route("/api/ahb/social/analytics/cleanup", methods=["GET"])
    def cleanup_list():
        raw = (request.args.get("older_than_days") or "90").strip()
        try:
            days = int(raw)
        except ValueError:
            days = 90
        if days < 1:
            return jsonify({"error": "older_than_days must be >= 1"}), 400
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(
            timespec="seconds"
        )
        con = _db()
        try:
            rows = con.execute(
                "SELECT id, platform, variant, status, caption, posted_at, "
                "updated_at, asset_path, cover_path, archived_at "
                "FROM ahb_social_posts "
                "WHERE status='posted' "
                "  AND archived_at IS NULL "
                "  AND COALESCE(posted_at, updated_at) IS NOT NULL "
                "  AND COALESCE(posted_at, updated_at) < ? "
                "ORDER BY COALESCE(posted_at, updated_at) ASC",
                (cutoff,),
            ).fetchall()
        finally:
            con.close()
        items = []
        for r in rows:
            d = dict(r)
            asset_sz = _file_size(d.get("asset_path"))
            cover_sz = _file_size(d.get("cover_path"))
            d["total_bytes"] = asset_sz + cover_sz
            items.append(d)
        return jsonify({
            "items": items,
            "older_than_days": days,
            "count": len(items),
        })

    @bp.route("/api/ahb/social/analytics/cleanup/archive", methods=["POST"])
    def cleanup_archive():
        data = request.get_json(silent=True) or {}
        ids = _coerce_ids(data)
        if not ids:
            return jsonify({"error": "ids required (non-empty list)"}), 400
        # Determine cleanup-eligible set so we only move files for items that
        # actually qualify; ids outside this set still get archived_at stamped
        # but no file moves.
        con = _db()
        try:
            placeholders = ",".join("?" * len(ids))
            elig_rows = con.execute(
                f"SELECT id, asset_path, cover_path FROM ahb_social_posts "
                f"WHERE id IN ({placeholders}) "
                f"  AND status='posted' "
                f"  AND archived_at IS NULL",
                ids,
            ).fetchall()
            eligible = {r["id"]: r for r in elig_rows}
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            archive_dir = os.path.join(_ARCHIVE_ROOT, date_str)
            errors: list = []
            archived = 0
            now = datetime.utcnow().isoformat(timespec="seconds")
            for pid in ids:
                # Stamp archived_at regardless of file presence (don't lose
                # data on a file error — the row mark is the source of truth).
                try:
                    con.execute(
                        "UPDATE ahb_social_posts SET archived_at=?, updated_at=? "
                        "WHERE id=? AND archived_at IS NULL",
                        (now, now, pid),
                    )
                except sqlite3.Error as e:
                    errors.append(f"id {pid}: db update failed ({e})")
                    continue
                # Move files only for cleanup-eligible posts.
                row = eligible.get(pid)
                if row is not None:
                    seen_paths: set = set()
                    for col in ("asset_path", "cover_path"):
                        src = row[col]
                        if not src or not isinstance(src, str):
                            continue
                        if not os.path.exists(src):
                            continue
                        # Dedup: asset_path and cover_path can point at the
                        # same file (e.g. when the cover IS the asset). Only
                        # move it once.
                        if src in seen_paths:
                            continue
                        seen_paths.add(src)
                        if not _path_is_safe(src):
                            errors.append(
                                f"id {pid}: refusing to touch unsafe path ({col})"
                            )
                            continue
                        try:
                            os.makedirs(archive_dir, exist_ok=True)
                            base = os.path.basename(src)
                            # Prefix with post id to avoid collisions inside
                            # the per-day archive bucket.
                            dst = os.path.join(
                                archive_dir, f"{pid}_{base}"
                            )
                            shutil.move(src, dst)
                        except (OSError, shutil.Error) as e:
                            errors.append(
                                f"id {pid}: move {col} failed ({e})"
                            )
                archived += 1
            con.commit()
        finally:
            con.close()
        return jsonify({"archived": archived, "errors": errors})

    @bp.route("/api/ahb/social/analytics/cleanup/delete", methods=["POST"])
    def cleanup_delete():
        data = request.get_json(silent=True) or {}
        ids = _coerce_ids(data)
        if not ids:
            return jsonify({"error": "ids required (non-empty list)"}), 400
        con = _db()
        try:
            placeholders = ",".join("?" * len(ids))
            rows = con.execute(
                f"SELECT id, asset_path, cover_path FROM ahb_social_posts "
                f"WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            errors: list = []
            deleted = 0
            for r in rows:
                pid = r["id"]
                seen_paths: set = set()
                for col in ("asset_path", "cover_path"):
                    p = r[col]
                    if not p or not isinstance(p, str):
                        continue
                    if not os.path.exists(p):
                        continue
                    # Dedup: asset_path and cover_path may refer to the same
                    # underlying file — don't try to unlink it twice.
                    if p in seen_paths:
                        continue
                    seen_paths.add(p)
                    if not _path_is_safe(p):
                        errors.append(
                            f"id {pid}: refusing to touch unsafe path ({col})"
                        )
                        continue
                    try:
                        os.unlink(p)
                    except OSError as e:
                        errors.append(f"id {pid}: unlink {col} failed ({e})")
                try:
                    con.execute(
                        "DELETE FROM ahb_social_post_tags WHERE post_id=?",
                        (pid,),
                    )
                    con.execute(
                        "DELETE FROM ahb_social_analytics WHERE post_id=?",
                        (pid,),
                    )
                    con.execute(
                        "DELETE FROM ahb_social_posts WHERE id=?", (pid,)
                    )
                    deleted += 1
                except sqlite3.Error as e:
                    errors.append(f"id {pid}: db delete failed ({e})")
            con.commit()
        finally:
            con.close()
        return jsonify({"deleted": deleted, "errors": errors})
