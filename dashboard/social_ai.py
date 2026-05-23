"""Social Studio v2.1 — AI route handlers.

These extend the v1 AI suite (caption/hashtags/hooks/score/translate in
social_studio.py) with pattern-driven hooks, CTA, comment-bait, and
voiceover script generation.

Helpers (_call_ollama_chat, _pick_copy_model, _extract_json_array,
_sources_summary) live in social_studio.py — imported on register so the
single source of truth stays there.
"""
from __future__ import annotations


_HOOK_PATTERNS = (
    "curiosity_gap", "contrarian", "number_led", "before_after",
    "personal", "mistake", "bold_claim",
)


def register(bp):
    """Register all v2.1 /ai/* routes on the given Blueprint."""
    from flask import jsonify, request

    # Import bare names first so we share the module object with tests that
    # `import social_studio` directly. Falls back to the dashboard.* path
    # for code paths where only the package import is registered.
    import sys
    _ss = sys.modules.get("social_studio")
    if _ss is None:
        try:
            from dashboard import social_studio as _ss
        except ImportError:
            import social_studio as _ss
    _settings = sys.modules.get("social_settings")
    if _settings is None:
        try:
            from dashboard import social_settings as _settings
        except ImportError:
            import social_settings as _settings

    @bp.route("/api/ahb/social/ai/hook", methods=["POST"])
    def social_ai_hook():
        data = request.get_json(silent=True) or {}
        pattern = (data.get("pattern") or "curiosity_gap").strip()
        if pattern not in _HOOK_PATTERNS:
            return jsonify({
                "error": "unknown pattern",
                "patterns": list(_HOOK_PATTERNS),
            }), 400
        n = int(data.get("n") or 3)
        n = max(1, min(n, 8))
        sys_prompt = _settings.load_prompt("hooks_advanced")
        user = (
            f"Pattern: {pattern}\n"
            f"N: {n}\n"
            f"Source media:\n{_ss._sources_summary(data.get('source_ids') or [])}\n"
        )
        model = data.get("model") or _ss._pick_copy_model()
        raw = _ss._call_ollama_chat(model, sys_prompt, user, temperature=0.9)
        hooks = _ss._extract_json_array(raw)[:n]
        return jsonify({"hooks": hooks, "pattern": pattern, "model": model})

    @bp.route("/api/ahb/social/ai/cta", methods=["POST"])
    def social_ai_cta():
        data = request.get_json(silent=True) or {}
        caption = (data.get("caption") or "").strip()
        if not caption:
            return jsonify({"error": "caption required"}), 400
        platform = data.get("platform") or "ig_reel"
        sys_prompt = _settings.load_prompt("cta_system")
        user = f"Caption: {caption}\nPlatform: {platform}\n"
        model = data.get("model") or _ss._pick_copy_model()
        raw = _ss._call_ollama_chat(model, sys_prompt, user, temperature=0.6)
        ctas = _ss._extract_json_array(raw)[:3]
        return jsonify({"ctas": ctas, "model": model})

    @bp.route("/api/ahb/social/ai/comment-bait", methods=["POST"])
    def social_ai_comment_bait():
        data = request.get_json(silent=True) or {}
        caption = (data.get("caption") or "").strip()
        if not caption:
            return jsonify({"error": "caption required"}), 400
        platform = data.get("platform") or "ig_reel"
        sys_prompt = _settings.load_prompt("comment_bait")
        user = f"Caption: {caption}\nPlatform: {platform}\n"
        model = data.get("model") or _ss._pick_copy_model()
        raw = _ss._call_ollama_chat(model, sys_prompt, user, temperature=0.7)
        baits = _ss._extract_json_array(raw)[:3]
        return jsonify({"prompts": baits, "model": model})

    @bp.route("/api/ahb/social/ai/translate-all", methods=["POST"])
    def social_ai_translate_all():
        data = request.get_json(silent=True) or {}
        caption = (data.get("caption") or "").strip()
        if not caption:
            return jsonify({"error": "caption required"}), 400
        hashtags = (data.get("hashtags") or "").strip()
        targets = data.get("targets")
        if not targets:
            targets = _settings.load_settings().get("translation_targets") or ["es"]
        # Cap to 5 to bound model calls per request
        targets = [t for t in targets if isinstance(t, str) and t.strip()][:5]
        if not targets:
            return jsonify({"error": "no valid targets"}), 400
        model = data.get("model") or _ss._pick_copy_model()
        translations = {}
        for lang in targets:
            sys_prompt = (
                f"You are a translator. Translate the user's text into {lang}. "
                f"Output only the translation. Preserve hashtags, emoji, and line breaks."
            )
            t_caption = _ss._call_ollama_chat(model, sys_prompt, caption, temperature=0.2).strip()
            t_hashtags = ""
            if hashtags:
                t_hashtags = _ss._call_ollama_chat(model, sys_prompt, hashtags, temperature=0.2).strip()
            translations[lang] = {"caption": t_caption, "hashtags": t_hashtags}
        # Optional: persist on a post via ?post_id=
        post_id = request.args.get("post_id", type=int)
        if post_id:
            import json as _json
            con = _ss._conn()
            try:
                con.execute(
                    "UPDATE ahb_social_posts SET translations=? WHERE id=?",
                    (_json.dumps(translations), post_id),
                )
                con.commit()
            finally:
                con.close()
        return jsonify({"translations": translations, "targets": targets, "model": model})

    @bp.route("/api/ahb/social/ai/cover-pick", methods=["POST"])
    def social_ai_cover_pick():
        import base64
        import json as _json
        import os as _os
        import subprocess
        import tempfile

        try:
            import requests  # ollama HTTP
        except ImportError:
            return jsonify({"error": "requests not installed"}), 500

        data = request.get_json(silent=True) or {}
        post_id = data.get("post_id")
        if post_id is None:
            return jsonify({"error": "post_id required"}), 400
        try:
            post_id = int(post_id)
        except (TypeError, ValueError):
            return jsonify({"error": "post_id must be int"}), 400

        con = _ss._conn()
        try:
            row = con.execute(
                "SELECT asset_path, cover_path FROM ahb_social_posts WHERE id=?",
                (post_id,),
            ).fetchone()
        finally:
            con.close()
        if not row:
            return jsonify({"error": "post not found"}), 404
        asset = row["asset_path"]
        if not asset or not _os.path.exists(asset):
            return jsonify({"error": "post has no rendered asset"}), 400
        # Probe duration
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", asset],
                check=True, capture_output=True, timeout=10,
            )
            duration = float(out.stdout.decode().strip())
        except Exception as e:
            return jsonify({"error": "ffprobe failed", "detail": str(e)[-200:]}), 500
        if duration <= 0:
            return jsonify({"error": "asset has no duration"}), 400

        # Extract 5 candidate frames
        tmpdir = tempfile.mkdtemp(prefix="coverpick_")
        frame_paths = []
        for i, frac in enumerate((0.0, 0.25, 0.50, 0.75, 0.95)):
            t = duration * frac
            fp = _os.path.join(tmpdir, f"f{i}.jpg")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", asset,
                     "-frames:v", "1", "-q:v", "3", fp],
                    check=True, capture_output=True, timeout=15,
                )
                if _os.path.exists(fp) and _os.path.getsize(fp) > 0:
                    frame_paths.append(fp)
            except subprocess.CalledProcessError:
                continue
        if not frame_paths:
            return jsonify({"error": "frame extraction failed"}), 500

        settings = _settings.load_settings()
        model = data.get("model") or settings.get("vision_model") or "qwen3-vl:latest"
        # b64-encode each frame
        images_b64 = []
        for fp in frame_paths:
            with open(fp, "rb") as fh:
                images_b64.append(base64.b64encode(fh.read()).decode("ascii"))

        prompt = (
            "You are selecting a video thumbnail. I will show you "
            f"{len(frame_paths)} candidate frames in order (index 0 first). "
            "Pick the SINGLE best thumbnail — the one most likely to make "
            "someone stop scrolling: subject clearly visible, in focus, "
            "no motion blur, expressive moment. "
            'Respond with ONLY a JSON object like {"index": N} where N is '
            "the 0-based index of the best frame."
        )

        # Try Ollama generate endpoint
        try:
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "images": images_b64,
                    "stream": False,
                    "options": {"temperature": 0.0},
                },
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
            response_text = body.get("response") or ""
        except requests.RequestException as e:
            return jsonify({"error": "vision call failed", "detail": str(e)[-200:]}), 500

        # Parse {"index": N}
        idx = 0
        try:
            start = response_text.find("{")
            end = response_text.rfind("}")
            if start >= 0 and end > start:
                obj = _json.loads(response_text[start:end + 1])
                idx = int(obj.get("index") or 0)
        except Exception:
            idx = 0
        idx = max(0, min(idx, len(frame_paths) - 1))

        # Copy winning frame to cover.jpg next to the asset
        cover_path = _os.path.splitext(asset)[0] + "_cover.jpg"
        try:
            with open(frame_paths[idx], "rb") as src_f, open(cover_path, "wb") as dst_f:
                dst_f.write(src_f.read())
        except OSError as e:
            return jsonify({"error": "cover write failed", "detail": str(e)[-200:]}), 500
        finally:
            # Cleanup tempdir
            for fp in frame_paths:
                try:
                    _os.remove(fp)
                except OSError:
                    pass
            try:
                _os.rmdir(tmpdir)
            except OSError:
                pass

        # Persist on post
        con = _ss._conn()
        try:
            con.execute(
                "UPDATE ahb_social_posts SET cover_path=? WHERE id=?",
                (cover_path, post_id),
            )
            con.commit()
        finally:
            con.close()
        return jsonify({
            "ok": True,
            "cover_path": cover_path,
            "picked_index": idx,
            "candidates": len(frame_paths),
            "model": model,
        })

    @bp.route("/api/ahb/social/ai/storyboard", methods=["POST"])
    def social_ai_storyboard():
        import json as _json

        data = request.get_json(silent=True) or {}
        desc = (data.get("project_description") or "").strip()
        if not desc:
            return jsonify({"error": "project_description required"}), 400
        try:
            duration = float(data.get("duration") or 20)
        except (TypeError, ValueError):
            duration = 20.0
        duration = max(5.0, min(duration, 120.0))
        style = data.get("style") or "pro"
        sys_prompt = _settings.load_prompt("storyboard")
        user = f"Project: {desc}\nDuration: {duration:g}\nStyle: {style}\n"
        model = data.get("model") or _ss._pick_copy_model()
        raw = _ss._call_ollama_chat(model, sys_prompt, user, temperature=0.7)
        # Parse — prefer reusing _extract_json_array, falling back to a permissive parse.
        shots = []
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                shots = parsed
        except Exception:
            try:
                start = raw.find("[")
                end = raw.rfind("]")
                if start >= 0 and end > start:
                    shots = _json.loads(raw[start:end + 1])
            except Exception:
                shots = []
        # Normalize: keep only dicts with required keys
        norm = []
        for s in shots:
            if not isinstance(s, dict):
                continue
            norm.append({
                "shot_type": str(s.get("shot_type") or "medium"),
                "subject": str(s.get("subject") or "").strip(),
                "duration_sec": float(s.get("duration_sec") or 2.5),
                "voiceover_line": str(s.get("voiceover_line") or "").strip(),
            })
        return jsonify({"shots": norm[:10], "duration": duration, "style": style, "model": model})

    @bp.route("/api/ahb/social/ai/broll", methods=["POST"])
    def social_ai_broll():
        data = request.get_json(silent=True) or {}
        caption = (data.get("caption") or "").strip()
        if not caption:
            return jsonify({"error": "caption required"}), 400
        sys_prompt = _settings.load_prompt("broll")
        user = (
            f"Caption: {caption}\n"
            f"Existing media:\n{_ss._sources_summary(data.get('source_ids') or [])}\n"
        )
        model = data.get("model") or _ss._pick_copy_model()
        raw = _ss._call_ollama_chat(model, sys_prompt, user, temperature=0.7)
        suggestions = _ss._extract_json_array(raw)[:5]
        return jsonify({"suggestions": suggestions, "model": model})

    @bp.route("/api/ahb/social/ai/predict", methods=["POST"])
    def social_ai_predict():
        data = request.get_json(silent=True) or {}
        caption = (data.get("caption") or "").strip()
        hashtags = (data.get("hashtags") or "").strip()
        hook = (data.get("hook") or "").strip()
        platform = data.get("platform") or "ig_reel"
        # Heuristic scoring (no model call — fast, deterministic)
        base = 1500  # mid view baseline per platform
        platform_base = {
            "tiktok": 4500, "ig_reel": 2200, "ig_story": 800,
            "ig_feed_square": 1200, "ig_feed_portrait": 1400,
        }
        base = platform_base.get(platform, 1500)
        improvements = []
        score_mod = 1.0
        # Hook
        if not hook:
            improvements.append("Add a hook overlay — single biggest leverage on retention.")
            score_mod *= 0.65
        else:
            hl = len(hook)
            if hl > 60:
                improvements.append(f"Shorten hook to ≤ 60 chars (currently {hl}).")
                score_mod *= 0.9
            elif hl < 12:
                improvements.append("Hook is very short — give the viewer one specific promise.")
                score_mod *= 0.92
        # Caption length
        cl = len(caption)
        if platform in ("tiktok", "ig_reel"):
            if cl < 40:
                improvements.append("Caption is short for video platforms — add 1-2 lines of context.")
                score_mod *= 0.9
            elif cl > 400:
                improvements.append("Caption is long — TikTok/Reels viewers skim. Trim to 2-3 lines.")
                score_mod *= 0.93
        elif platform.startswith("ig_feed"):
            if cl < 80:
                improvements.append("Feed posts reward longer captions — expand to a short story.")
                score_mod *= 0.88
        # Hashtag count
        tag_count = len([t for t in hashtags.split() if t.startswith("#")])
        if platform.startswith("ig_") and tag_count < 5:
            improvements.append(f"Only {tag_count} hashtags — IG rewards 8-15 tags.")
            score_mod *= 0.9
        if tag_count > 20:
            improvements.append("Too many hashtags — looks spammy.")
            score_mod *= 0.85
        improvements = improvements[:3]
        if not improvements:
            improvements = [
                "Looks solid. A/B test 2 alt hooks before posting.",
                "Schedule into a high-traffic slot from /best-times.",
                "Consider adding a comment-bait line to drive engagement.",
            ]
        mid = int(base * score_mod)
        low = int(mid * 0.55)
        high = int(mid * 2.1)
        confidence = "low" if score_mod < 0.8 else ("medium" if score_mod < 0.95 else "high")
        return jsonify({
            "view_range": {"low": low, "mid": mid, "high": high},
            "confidence": confidence,
            "improvements": improvements,
        })

    @bp.route("/api/ahb/social/best-times", methods=["GET"])
    def social_best_times():
        platform = request.args.get("platform") or "ig_reel"
        # Industry-default slots until ahb_social_analytics has signal.
        # Format: (day_of_week 0=Mon, hour 0-23, score 0-100)
        defaults = {
            "tiktok": [(0,19,84),(1,18,82),(2,21,86),(3,19,90),(4,16,78),(5,11,72),(6,20,76)],
            "ig_reel": [(0,12,80),(1,11,82),(2,12,86),(3,18,88),(4,17,84),(5,10,70),(6,11,74)],
            "ig_feed_square": [(0,11,78),(1,12,80),(2,13,82),(3,17,84),(4,15,80),(5,10,68),(6,11,72)],
            "ig_feed_portrait": [(0,11,78),(1,12,80),(2,13,82),(3,17,84),(4,15,80),(5,10,68),(6,11,72)],
            "ig_story": [(0,8,70),(1,8,72),(2,8,74),(3,8,76),(4,8,72),(5,9,66),(6,9,68)],
        }
        slots = defaults.get(platform, defaults["ig_reel"])
        return jsonify({
            "platform": platform,
            "source": "industry_defaults",
            "slots": [{"day_of_week": d, "hour": h, "score": s} for (d, h, s) in slots],
        })

    @bp.route("/api/ahb/social/ai/voiceover-script", methods=["POST"])
    def social_ai_voiceover_script():
        data = request.get_json(silent=True) or {}
        caption = (data.get("caption") or "").strip()
        if not caption:
            return jsonify({"error": "caption required"}), 400
        sys_prompt = _settings.load_prompt("voiceover_script")
        user = (
            f"Caption:\n{caption}\n\n"
            f"Source media:\n{_ss._sources_summary(data.get('source_ids') or [])}\n"
        )
        model = data.get("model") or _ss._pick_copy_model()
        script = _ss._call_ollama_chat(model, sys_prompt, user, temperature=0.5).strip()
        return jsonify({"script": script, "model": model})
