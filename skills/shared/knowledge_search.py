#!/usr/bin/env python3
"""
Baza Empire — Unified Knowledge Search

Searches the FTS5 index `baza_knowledge_fts` (built by knowledge_rebuild_index)
across all AHBCO business data + shared agent memory.

SKILL_ARGS:
  query : "plumbing receipt home depot"       (required, free text)
  sources : ["receipt","document","project"]  (optional — filter by source type)
  limit : 10                                   (default 10)

Returns: JSON with ranked hits { source, source_id, title, body_snippet, rank }.
Use this before answering any question about AHBCO projects, receipts, debts,
invoices, documents, or empire knowledge — it lets every agent reach into the
full corpus without reading hundreds of rows first.
"""
import os, sys, json, sqlite3, re

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(FRAMEWORK, "dashboard", "baza_projects.db")

def _sanitize(q: str) -> str:
    # FTS5 MATCH syntax: strip dangerous chars, wrap phrases in quotes per-term
    terms = re.findall(r'[\w\-\.@]+', q.lower())
    if not terms:
        return ""
    return " OR ".join(f'"{t}"*' for t in terms[:12])  # prefix-match, OR-joined


def search(query: str, sources=None, limit=10):
    if not query or not query.strip():
        return {"ok": False, "error": "query is required"}
    fts_q = _sanitize(query)
    if not fts_q:
        return {"ok": False, "error": "query has no searchable terms"}
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    where_source = ""
    params = [fts_q]
    if sources:
        where_source = "AND source IN (" + ",".join("?" * len(sources)) + ")"
        params.extend(sources)
    sql = f"""
        SELECT source, source_id, title, body, tags, rank
        FROM baza_knowledge_fts
        WHERE baza_knowledge_fts MATCH ? {where_source}
        ORDER BY rank
        LIMIT {int(limit)}
    """
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        return {"ok": False, "error": f"FTS index missing — run knowledge_rebuild_index first ({e})"}
    hits = []
    for r in rows:
        body = r["body"] or ""
        snippet = (body[:280] + "...") if len(body) > 280 else body
        hits.append({
            "source": r["source"],
            "source_id": r["source_id"],
            "title": r["title"],
            "snippet": snippet,
            "tags": r["tags"],
        })
    total = conn.execute(
        f"SELECT COUNT(*) FROM baza_knowledge_fts WHERE baza_knowledge_fts MATCH ? {where_source}",
        params,
    ).fetchone()[0]
    conn.close()
    return {"ok": True, "query": query, "total": total, "returned": len(hits), "hits": hits}


if __name__ == "__main__":
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    result = search(
        query=args.get("query", ""),
        sources=args.get("sources"),
        limit=int(args.get("limit", 10)),
    )
    print(json.dumps(result, default=str))
    sys.exit(0 if result.get("ok") else 1)
