#!/usr/bin/env python3
"""
Baza Empire — Artifact Auto-Save Skill
All agents call this when they create or modify any file.
Preserves file type, saves to dashboard artifacts dir, logs to AHBAgentReport.

Usage:
    from skills.shared.artifact_save import save_artifact
    save_artifact(agent_id="claw_batto", project_id="ahb123", file_name="homepage.html", content="...", description="Built homepage")
"""
import os, json
from pathlib import Path
from datetime import datetime

FRAMEWORK_DIR   = Path(__file__).resolve().parent.parent.parent
ARTIFACTS_DIR   = FRAMEWORK_DIR / "dashboard" / "artifacts"

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

    # Save locally — project_id flat dir (matches dashboard scanner)
    dest_dir = ARTIFACTS_DIR / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file_name

    try:
        dest_path.write_text(content, encoding='utf-8')
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {
        "success":  True,
        "path":     str(dest_path),
        "rel":      f"{project_id}/{file_name}",
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
    dest_dir = ARTIFACTS_DIR / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file_name
    dest_path.write_bytes(data)
    return {
        "success": True,
        "path":    str(dest_path),
        "rel":     f"{project_id}/{file_name}",
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

# ── Skill execution (called by SkillsEngine via SKILL_ARGS env var) ──────────
if __name__ == "__main__":
    import sys
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    agent_id = args.get("agent_id") or os.environ.get("AGENT_ID", "unknown")

    # Support both naming conventions: filename/file_name, content, project_id
    file_name = args.get("filename") or args.get("file_name", "")
    content   = args.get("content", "")
    project_id = args.get("project_id", "shared")
    description = args.get("description", "")
    task_id    = args.get("task_id", "")
    file_path  = args.get("file_path", "")

    if not file_name and not file_path:
        print(json.dumps({"success": False, "error": "filename or file_path required"}))
        sys.exit(1)

    result = save_artifact(
        agent_id=agent_id,
        file_name=file_name,
        content=content,
        project_id=project_id,
        description=description,
        task_id=task_id,
        file_path=file_path,
    )
    if result.get("success"):
        print(f"Saved: {file_name} -> {result.get('path','')}")
        print(f"Project: {project_id} | Size: {result.get('size', 0)} bytes")
    else:
        print(f"Error: {result.get('error', 'unknown')}")
    print(json.dumps(result))
