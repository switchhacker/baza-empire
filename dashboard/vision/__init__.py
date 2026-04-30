"""Baza Vision — image catalogue engine.

Sub-modules:
  db          — SQLite connection + schema bootstrapping
  taxonomy    — virtual folder tree definitions
  classifier  — qwen3-vl structured-attribute extraction
  cropper     — InsightFace + qwen-bbox crop pipeline
  search      — FTS5 + attribute filter composer
  ingest      — observing new files into the catalogue
  seed_scan   — Specter mode 1 (gap detector)
  seed_fulfill — Specter mode 2 (worker: scrape + generate)
"""
