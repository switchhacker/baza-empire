"""Loader for config/scaffold.yaml — the master switch for the skill scaffold.
Flag-off (default) means agents run the legacy single-shot / two-pass path."""
import os
import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "config", "scaffold.yaml")
_DEFAULTS = {"enabled": False, "max_steps": 6, "retrieval_top_k": 8,
             "pinned_core": ["artifact_save", "web_search", "ahb123_query",
                             "skill_search", "call_tool"],
             "per_agent": {}}
_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    data = {}
    try:
        with open(_CONFIG_PATH) as f:
            data = (yaml.safe_load(f) or {}).get("scaffold", {}) or {}
    except FileNotFoundError:
        data = {}
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in data.items() if v is not None})
    _cache = merged
    return merged


def reload():
    """Drop the cache (used by tests / after editing the yaml)."""
    global _cache
    _cache = None
    return _load()


def is_enabled(agent_id: str | None = None) -> bool:
    cfg = _load()
    if agent_id:
        override = (cfg.get("per_agent") or {}).get(agent_id) or {}
        if "enabled" in override:
            return bool(override["enabled"])
    return bool(cfg.get("enabled", False))


def max_steps() -> int:
    return int(_load().get("max_steps", 6))


def retrieval_top_k() -> int:
    return int(_load().get("retrieval_top_k", 8))


def pinned_core() -> list[str]:
    return list(_load().get("pinned_core", []))
