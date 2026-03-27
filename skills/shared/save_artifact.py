#!/usr/bin/env python3
"""
Baza Empire — Save Artifact Skill
Agents call this to persist any file (html, json, py, md, sh, csv, etc.)
to the dashboard artifacts folder so Serge can view/download it.

Usage (from agent or CLI):
    from skills.shared.save_artifact import save_artifact
    url = save_artifact(
        filename="report.html",
        content="<html>...</html>",
        project_id="proj-ahb123",
        agent_id="simon_bately",
        task_id="abc123"   # optional
    )

CLI:
    python save_artifact.py --filename report.md --project proj-ahb123 \
                            --agent simon_bately --content "# Hello"
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error

DASHBOARD_URL = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")

SUPPORTED_EXTENSIONS = [
    # Documents
    ".txt", ".md", ".rst", ".csv",
    # Web
    ".html", ".htm", ".css", ".js", ".ts", ".jsx", ".tsx",
    ".json", ".xml", ".yaml", ".yml",
    # Code
    ".py", ".sh", ".bash", ".env", ".toml", ".ini", ".cfg", ".conf",
    # Data / Logs
    ".sql", ".log",
    # Graphics (text-based)
    ".svg",
]


def save_artifact(
    filename: str,
    content: str,
    project_id: str = "shared",
    agent_id: str = "",
    task_id: str = "",
) -> dict:
    """
    POST content to the dashboard /api/artifacts/save-text endpoint.
    Returns dict: { success, name, project_id, size, download_url }
    """
    payload = json.dumps({
        "filename":   filename,
        "content":    content,
        "project_id": project_id,
        "agent_id":   agent_id,
        "task_id":    task_id,
    }).encode("utf-8")

    url = f"{DASHBOARD_URL}/api/artifacts/save-text"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result
    except urllib.error.URLError as e:
        return {"success": False, "error": str(e)}


def detect_project(content: str, default: str = "shared") -> str:
    """Guess project_id from content keywords."""
    low = content.lower()
    if any(k in low for k in ["ahb123", "home improvement", "ahbco", "all home building"]):
        return "proj-ahb123"
    if any(k in low for k in ["baza empire", "mining", "xmrig", "node fleet", "agent framework"]):
        return "proj-baza-empire"
    return default


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Save an artifact file to the Baza dashboard")
    parser.add_argument("--filename",   required=True,  help="Filename with extension, e.g. report.html")
    parser.add_argument("--content",    default="",     help="File content (string). Use @path to read from file.")
    parser.add_argument("--project",    default="shared", help="Project ID, e.g. proj-ahb123")
    parser.add_argument("--agent",      default="",     help="Agent ID, e.g. simon_bately")
    parser.add_argument("--task-id",    default="",     help="Task ID this artifact belongs to")
    parser.add_argument("--dashboard",  default="",     help="Dashboard URL override")
    args = parser.parse_args()

    if args.dashboard:
        DASHBOARD_URL = args.dashboard

    # Allow content from file
    content = args.content
    if content.startswith("@"):
        path = content[1:]
        try:
            content = open(path, "r", errors="ignore").read()
        except Exception as e:
            print(f"Error reading file {path}: {e}", file=sys.stderr)
            sys.exit(1)

    result = save_artifact(
        filename=args.filename,
        content=content,
        project_id=args.project,
        agent_id=args.agent,
        task_id=args.task_id,
    )

    if result.get("success"):
        print(f"✅ Saved: {result['name']} ({result.get('size', 0)} bytes)")
        print(f"   Project: {result['project_id']}")
        print(f"   Download: {DASHBOARD_URL}{result.get('download_url','')}")
    else:
        print(f"❌ Failed: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)
