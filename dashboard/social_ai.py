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
