#!/usr/bin/env python3
"""
Baza Empire — Document Reconciler

Backfills ahb_documents.project_id for docs where the curator did not set one.
Fuzzy-matches the doc's content_text + summary + entity against every project's
title / address / client_name / location and updates project_id when confidence
clears the threshold.

Docs about company-wide topics (HIC license, compliance, payroll, general
contracts with no project hint) correctly stay NULL — those are not project-
scoped and shouldn't be force-linked.

SKILL_ARGS:
  dry_run : true       (default false — preview without writing)
  threshold : 0.6      (default 0.6 — match score 0..1)
  limit : 200          (default 200 docs per run)

Returns: JSON { scanned, matched, updated, unmatched, samples: [...] }.
"""
import os, sys, json, sqlite3, re
from difflib import SequenceMatcher

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(FRAMEWORK, "dashboard", "baza_projects.db")


def _normalize(s: str) -> str:
    return re.sub(r'[^a-z0-9 ]+', ' ', (s or '').lower()).strip()


def _tokens(s: str) -> set:
    return {t for t in _normalize(s).split() if len(t) >= 3}


AHBCO_ALIASES = {
    "all home building", "all home building co", "all home building company",
    "ahbco", "ahbco llc", "all home building co llc",
}


def _is_self_reference(val_norm: str) -> bool:
    """Project fields that contain AHBCO itself are not usable for matching — they'd
    falsely link every company doc to the first project where AHBCO is the client."""
    return any(alias in val_norm for alias in AHBCO_ALIASES)


def _score(doc_text: str, project: dict) -> float:
    """Combine token overlap + address-substring + sequence ratio, weighted by field."""
    doc_norm = _normalize(doc_text)
    doc_toks = _tokens(doc_text)
    if not doc_toks:
        return 0.0

    # Field weights: address is the strongest signal; title next; client_name weakest
    # because many projects list AHBCO itself as client.
    weights = {"address": 1.0, "title": 0.85, "location": 0.9, "client_name": 0.6}
    min_substring_len = {"address": 10, "title": 8, "location": 10, "client_name": 12}

    best = 0.0
    for field, weight in weights.items():
        val = project.get(field) or ""
        val_norm = _normalize(val)
        val_toks = _tokens(val)
        if not val_toks or len(val_norm) < 4:
            continue
        if _is_self_reference(val_norm):
            continue  # skip AHBCO-as-client fields
        # substring hit — only trust if the raw value is specific enough
        sub = 1.0 if val_norm in doc_norm and len(val_norm) >= min_substring_len[field] else 0.0
        jacc = len(doc_toks & val_toks) / max(1, len(doc_toks | val_toks))
        seq = SequenceMatcher(None, val_norm, doc_norm[:500]).ratio()
        combined = weight * max(sub, 0.5 * jacc + 0.5 * seq)
        if combined > best:
            best = combined
    return best


def reconcile(dry_run=False, threshold=0.6, limit=200):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    projects = [dict(r) for r in conn.execute(
        "SELECT id AS project_id, title, address, client_name, location FROM ahb_projects"
    ).fetchall()]

    unlinked = conn.execute(
        "SELECT id, doc_type, entity, summary, content_text, suggested_name "
        "FROM ahb_documents WHERE project_id IS NULL OR project_id='' "
        "ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()

    matched = []
    unmatched = []
    for doc in unlinked:
        doc_text = " ".join(filter(None, [
            doc["entity"], doc["summary"], doc["suggested_name"],
            (doc["content_text"] or "")[:2000],
        ]))
        best_p = None
        best_s = 0.0
        for p in projects:
            s = _score(doc_text, p)
            if s > best_s:
                best_s = s
                best_p = p
        if best_p and best_s >= threshold:
            matched.append({
                "doc_id": doc["id"],
                "doc_type": doc["doc_type"],
                "entity": doc["entity"],
                "project_id": best_p["project_id"],
                "project_title": best_p["title"],
                "score": round(best_s, 3),
            })
        else:
            unmatched.append({
                "doc_id": doc["id"],
                "doc_type": doc["doc_type"],
                "entity": doc["entity"],
                "best_score": round(best_s, 3) if best_p else 0.0,
            })

    updated = 0
    if not dry_run and matched:
        for m in matched:
            conn.execute(
                "UPDATE ahb_documents SET project_id=? WHERE id=?",
                (m["project_id"], m["doc_id"]),
            )
            updated += 1
        conn.commit()
    conn.close()

    return {
        "ok": True,
        "dry_run": dry_run,
        "scanned": len(unlinked),
        "matched": len(matched),
        "updated": updated,
        "unmatched": len(unmatched),
        "sample_matched": matched[:5],
        "sample_unmatched": unmatched[:5],
    }


if __name__ == "__main__":
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    print(json.dumps(reconcile(
        dry_run=bool(args.get("dry_run", False)),
        threshold=float(args.get("threshold", 0.6)),
        limit=int(args.get("limit", 200)),
    ), default=str))
