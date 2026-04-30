"""Curated CC0/CC-BY image scraper.

Reads scrape_sources.yaml, picks an enabled source whose API key is set,
runs the search, downloads N images to artifacts/.vision-scraped/<source>/<date>/,
returns the local paths. Rate-limited per source.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Optional

import yaml

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPE_DIR = os.path.join(DASHBOARD_DIR, "artifacts", ".vision-scraped")
SOURCES_PATH = os.path.join(os.path.dirname(__file__), "scrape_sources.yaml")
LAST_REQUEST_AT: dict[str, float] = {}


def _load_sources() -> dict:
    with open(SOURCES_PATH) as fh:
        return yaml.safe_load(fh)


def _walk_path(obj, dotted: str):
    """Walk obj following dotted keys; return None on miss."""
    cur = obj
    for k in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return None
        if cur is None:
            return None
    return cur


def _rate_limit(source_id: str, seconds: float):
    last = LAST_REQUEST_AT.get(source_id, 0)
    delta = time.time() - last
    if delta < seconds:
        time.sleep(seconds - delta)
    LAST_REQUEST_AT[source_id] = time.time()


def _enabled_sources(cfg: dict) -> list[dict]:
    out = []
    for src in cfg.get("sources", []):
        if not src.get("enabled"):
            continue
        env = src.get("auth_value_env") or ""
        if env and not os.getenv(env):
            continue   # api key missing — skip
        out.append(src)
    return out


def _query_for_path(cfg: dict, path: str) -> Optional[str]:
    return cfg.get("queries", {}).get(path)


def _http_get_json(url: str, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as resp:
        with open(dest, "wb") as fh:
            fh.write(resp.read())


def _safe_filename(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)[:100]


def scrape_for_path(taxonomy_path: str, *, count: int = 6) -> list[tuple[str, str]]:
    """Returns list of (abs_path, origin_url) for downloaded images."""
    cfg = _load_sources()
    query = _query_for_path(cfg, taxonomy_path)
    if not query:
        raise ValueError(f"no scrape query mapped for {taxonomy_path}")

    enabled = _enabled_sources(cfg)
    if not enabled:
        raise RuntimeError("no enabled scrape sources have API keys configured")

    src = enabled[0]   # pick the first; round-robin can wait for v2
    _rate_limit(src["id"], src.get("rate_limit_seconds", 2))

    # Compose URL
    params = {
        src["query_param"]: query,
        src["per_page_param"]: min(count, src.get("per_page_default", 6)),
    }
    extra = src.get("extra_params") or {}
    for k, v in (extra.get("static") or {}).items():
        params[k] = v
    if extra.get("key_env"):
        params["key"] = os.getenv(extra["key_env"], "")

    url = src["base_url"] + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "BazaSpecter/1.0 (+vision-seeder)"}
    if src.get("auth_header"):
        env = src.get("auth_value_env") or ""
        prefix = src.get("auth_prefix") or ""
        headers[src["auth_header"]] = prefix + os.getenv(env, "")

    data = _http_get_json(url, headers)
    items = _walk_path(data, src["response_path"]["list"]) or []

    today = time.strftime("%Y-%m-%d")
    out_dir = os.path.join(SCRAPE_DIR, src["id"], today)
    results: list[tuple[str, str]] = []
    for n, item in enumerate(items[:count]):
        url_path = src["response_path"]["url"]
        if url_path == "__wikimedia_title__":
            # Wikimedia returns titles; need a second API hop to get the actual URL.
            # Skipped for v1 — Wikimedia stays disabled in practice unless the user
            # adds a per-title resolution pass.
            continue
        img_url = _walk_path(item, url_path)
        if not img_url:
            continue
        ext = ".jpg"
        if ".png" in img_url.lower():
            ext = ".png"
        dest = os.path.join(out_dir, _safe_filename(f"{src['id']}_{n}{ext}"))
        try:
            _download(img_url, dest)
            results.append((dest, img_url))
            _rate_limit(src["id"], src.get("rate_limit_seconds", 2))
        except Exception as e:
            print(f"[scrape-fail] {img_url}: {e}")
    return results
