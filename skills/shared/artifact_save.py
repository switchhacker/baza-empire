#!/usr/bin/env python3
"""
Baza Empire — Artifact Auto-Save Skill
All agents call this when they create or modify any file.
Preserves file type, saves to dashboard artifacts dir, logs to AHBAgentReport.

Usage:
    from skills.shared.artifact_save import save_artifact
    save_artifact(agent_id="claw_batto", project_id="ahb123", file_name="homepage.html", content="...", description="Built homepage")
"""
import os, json, requests
from pathlib import Path
from datetime import datetime

FRAMEWORK_DIR   = Path(__file__).resolve().parent.parent.parent
ARTIFACTS_DIR   = FRAMEWORK_DIR / "dashboard" / "artifacts"
DASHBOARD_URL   = os.environ.get("DASHBOARD_URL", "http://localhost:8888")

def save_artifact(
    agent_id:    str,
    file_name:   str,
    content:     str  = "",
    project_id:  str  = "shared",
    description: str  = "",
    tags:        list = None,
    task_id:     str  = "",
    file_path:   str  = "",   # if set, read content from this path instead
) -> dict:
    """
    Save a file as an artifact.
    - Preserves the original file extension exactly
    - Saves to dashboard/artifacts/<agent_id>/<project_id>/<file_name>
    - Returns {"success": True, "path": "...", "url": "..."}
    """
    tags = tags or []

    # If file_path provided, read from disk
    if file_path and not content:
        try:
            content = Path(file_path).read_text(encoding='utf-8', errors='replace')
            if not file_name:
                file_name = Path(file_path).name
        except Exception as e:
            return {"success": False, "error": f"Cannot read {file_path}: {e}"}

    if not file_name:
        return {"success": False, "error": "file_name required"}

    # Preserve original extension
    file_ext = Path(file_name).suffix.lower() or ".txt"

    # Save locally
    dest_dir = ARTIFACTS_DIR / agent_id / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file_name

    try:
        dest_path.write_text(content, encoding='utf-8')
    except Exception as e:
        return {"success": False, "error": str(e)}

    # Also notify dashboard API (non-blocking)
    try:
        requests.post(f"{DASHBOARD_URL}/api/artifacts/save-agent", json={
            "agent_id":    agent_id,
            "project_id":  project_id,
            "file_name":   file_name,
            "content":     content,
            "description": description,
            "tags":        tags,
            "file_ext":    file_ext,
            "task_id":     task_id,
        }, timeout=3)
    except:
        pass  # Dashboard offline — file still saved locally

    return {
        "success":  True,
        "path":     str(dest_path),
        "rel":      f"{agent_id}/{project_id}/{file_name}",
        "ext":      file_ext,
        "size":     len(content.encode('utf-8')),
    }

def save_binary_artifact(
    agent_id:   str,
    file_name:  str,
    data:       bytes,
    project_id: str = "shared",
    description:str = "",
) -> dict:
    """Save binary files (images, ZIPs, etc) as artifacts."""
    file_ext = Path(file_name).suffix.lower()
    dest_dir = ARTIFACTS_DIR / agent_id / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file_name
    dest_path.write_bytes(data)
    return {
        "success": True,
        "path":    str(dest_path),
        "rel":     f"{agent_id}/{project_id}/{file_name}",
        "ext":     file_ext,
        "size":    len(data),
    }

def list_agent_artifacts(agent_id: str, project_id: str = "") -> list:
    """List artifacts saved by a specific agent."""
    base = ARTIFACTS_DIR / agent_id
    if not base.exists():
        return []
    files = []
    search = base / project_id if project_id else base
    for p in sorted(search.rglob("*")):
        if p.is_file():
            files.append({
                "name":       p.name,
                "path":       str(p),
                "rel":        str(p.relative_to(ARTIFACTS_DIR)),
                "ext":        p.suffix.lower(),
                "size":       p.stat().st_size,
                "modified":   datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "project_id": p.parent.name,
                "agent_id":   agent_id,
            })
    return files

# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = save_artifact(
        agent_id="claw_batto",
        project_id="baza-framework",
        file_name="test_artifact.md",
        content="# Test\nThis is a test artifact saved by claw_batto.",
        description="Test artifact save",
    )
    print("Save result:", result)
    arts = list_agent_artifacts("claw_batto")
    print(f"Claw artifacts: {len(arts)} files")
    for a in arts[:5]:
        print(f"  {a['rel']} ({a['size']} bytes)")
