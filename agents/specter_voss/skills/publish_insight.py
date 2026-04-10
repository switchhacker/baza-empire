#!/usr/bin/env python3
"""
Specter Voss — Publish Insight to Data Hub
Saves an insight/report as an artifact accessible to all agents.
Tags with agent_id, category, timestamp.

Args:
    {
        "title": "Infrastructure Health Report",
        "content": "Full report text...",
        "category": "insight|alert|report|research",
        "project_id": "data-hub"
    }
"""
import os, sys, json
from datetime import datetime
from pathlib import Path

SKILL_ARGS = json.loads(os.environ.get("SKILL_ARGS", "{}"))
FRAMEWORK_DIR = Path(__file__).resolve().parent.parent.parent.parent

# Add framework root to path so we can import shared skills
sys.path.insert(0, str(FRAMEWORK_DIR))


def main():
    title = SKILL_ARGS.get("title", "").strip()
    content = SKILL_ARGS.get("content", "").strip()
    category = SKILL_ARGS.get("category", "insight").strip()
    project_id = SKILL_ARGS.get("project_id", "data-hub").strip()
    agent_id = SKILL_ARGS.get("agent_id") or os.environ.get("AGENT_ID", "specter_voss")

    if not title:
        print("ERROR: 'title' is required")
        return

    if not content:
        print("ERROR: 'content' is required")
        return

    valid_categories = {"insight", "alert", "report", "research"}
    if category not in valid_categories:
        print(f"WARNING: category '{category}' not in {valid_categories}, defaulting to 'insight'")
        category = "insight"

    # Build the artifact content with metadata header
    ts = datetime.now()
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
    date_slug = ts.strftime("%Y-%m-%d")

    # Generate filename from title
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title)
    safe_title = safe_title.strip().replace(" ", "_").lower()[:60]
    file_name = f"{category}_{safe_title}_{date_slug}.md"

    # Build document
    doc = f"""# {title}

| Field | Value |
|-------|-------|
| Category | {category} |
| Agent | {agent_id} |
| Timestamp | {ts_str} |
| Project | {project_id} |

---

{content}

---
*Published by {agent_id} via Specter Voss Data Hub*
"""

    # Use the shared artifact_save module
    try:
        from skills.shared.artifact_save import save_artifact

        result = save_artifact(
            agent_id=agent_id,
            file_name=file_name,
            content=doc,
            project_id=project_id,
            description=f"[{category.upper()}] {title}",
            tags=[category, agent_id, date_slug],
        )

        if result.get("success"):
            print(f"Insight published successfully.")
            print(f"  Title:    {title}")
            print(f"  Category: {category}")
            print(f"  File:     {file_name}")
            print(f"  Path:     {result.get('path', '?')}")
            print(f"  Size:     {result.get('size', 0)} bytes")
            print(f"  Project:  {project_id}")
        else:
            print(f"ERROR saving artifact: {result.get('error', 'unknown')}")
    except ImportError:
        # Fallback: save directly to artifacts directory
        print("WARNING: Could not import artifact_save, saving directly to disk")
        artifacts_dir = FRAMEWORK_DIR / "dashboard" / "artifacts" / project_id
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dest = artifacts_dir / file_name
        try:
            dest.write_text(doc, encoding="utf-8")
            print(f"Insight saved (fallback mode).")
            print(f"  Title:    {title}")
            print(f"  Category: {category}")
            print(f"  File:     {file_name}")
            print(f"  Path:     {dest}")
            print(f"  Size:     {len(doc.encode('utf-8'))} bytes")
        except Exception as e:
            print(f"ERROR: Failed to write file: {e}")

    # Also log to PostgreSQL empire_knowledge if possible
    try:
        import psycopg2
        db_config = {
            "host": os.environ.get("BAZA_DB_HOST", "localhost"),
            "port": int(os.environ.get("BAZA_DB_PORT", "5432")),
            "dbname": os.environ.get("BAZA_DB_NAME", "baza_agents"),
            "user": os.environ.get("BAZA_DB_USER", "switchhacker"),
            "password": os.environ.get("DB_PASSWORD", "baza2026"),
        }
        conn = psycopg2.connect(**db_config)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO empire_knowledge (key, value, category) VALUES (%s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, category = EXCLUDED.category",
            (f"specter_{category}_{safe_title}", content[:2000], category),
        )
        conn.commit()
        cur.close()
        conn.close()
        print(f"  Also logged to empire_knowledge table")
    except Exception as e:
        print(f"  Note: Could not log to empire_knowledge: {e}")


if __name__ == "__main__":
    main()
