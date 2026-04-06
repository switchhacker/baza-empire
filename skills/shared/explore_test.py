#!/usr/bin/env python3
"""
Baza Empire Skill — explore_test
Send files, artifacts, or URLs to the Explore Lab for testing in virtual device simulators.

SKILL_ARGS:
  artifact     (str)  — artifact filename to test (searches dashboard/artifacts/)
  project_id   (str)  — project folder (default: proj-ahb123)
  url          (str)  — URL to test directly
  file_path    (str)  — absolute path to file to test
  text         (str)  — raw HTML to test (creates temp file)
  title        (str)  — title for temp HTML (default: "Test Page")
  device       (str)  — device to test on (default: "chrome-desktop")
  action       (str)  — "test" (default), "list-devices", "sessions"

Examples:
  ##SKILL:explore_test{"artifact":"claw_contractor_cta.html","project_id":"proj-ahb123"}##
  ##SKILL:explore_test{"url":"http://localhost:8888"}##
  ##SKILL:explore_test{"text":"<h1>Hello</h1><p>Test page</p>","title":"Quick Test"}##
  ##SKILL:explore_test{"action":"list-devices"}##
"""
import os
import sys
import json
import uuid
import urllib.request
import urllib.error

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACTS_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")
EXPLORE_SESSIONS_DIR = os.path.join(ARTIFACTS_DIR, "explore-sessions")
DASHBOARD_URL = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

action     = args.get("action", "test").strip()
artifact   = args.get("artifact", "").strip()
project_id = args.get("project_id", "proj-ahb123").strip()
url        = args.get("url", "").strip()
file_path  = args.get("file_path", "").strip()
text       = args.get("text", "").strip()
title      = args.get("title", "Test Page").strip()
device     = args.get("device", "chrome-desktop").strip()


def result_json(success, explore_url="", device_name="", message="", extra=None):
    """Print result as JSON (last line) with a human-readable summary above."""
    obj = {
        "success": success,
        "explore_url": explore_url,
        "device": device_name,
        "message": message,
    }
    if extra:
        obj.update(extra)
    if success and explore_url:
        print(f"Explore Lab ready: {explore_url}")
        print(f"Device: {device_name}")
    elif success:
        print(message)
    else:
        print(f"Error: {message}", file=sys.stderr)
    print(json.dumps(obj))


def fetch_api(endpoint):
    """GET a dashboard API endpoint and return parsed JSON."""
    api_url = f"{DASHBOARD_URL}{endpoint}"
    try:
        req = urllib.request.Request(api_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def find_artifact(filename, proj):
    """Locate an artifact file in dashboard/artifacts/. Returns relative URL path or None."""
    # Check in specified project first
    candidate = os.path.join(ARTIFACTS_DIR, proj, filename)
    if os.path.isfile(candidate):
        return f"/artifacts/{proj}/{filename}"

    # Search all project folders
    if os.path.isdir(ARTIFACTS_DIR):
        for proj_dir in os.listdir(ARTIFACTS_DIR):
            candidate = os.path.join(ARTIFACTS_DIR, proj_dir, filename)
            if os.path.isfile(candidate):
                return f"/artifacts/{proj_dir}/{filename}"
    return None


# ── ACTION: list-devices ──
if action == "list-devices":
    data = fetch_api("/api/explore/devices")
    if "error" in data:
        result_json(False, message=f"Failed to fetch devices: {data['error']}")
        sys.exit(1)

    lines = ["Available devices in Explore Lab:"]
    for category, devices in data.items():
        lines.append(f"\n  {category.upper()}:")
        for d in devices:
            name = d.get("name", "unknown")
            w = d.get("w", "?")
            h = d.get("h", "?")
            lines.append(f"    - {name} ({w}x{h})")

    result_json(True, message="\n".join(lines), extra={"devices": data})
    sys.exit(0)

# ── ACTION: sessions ──
if action == "sessions":
    data = fetch_api("/api/explore/sessions")
    if "error" in data:
        result_json(False, message=f"Failed to fetch sessions: {data['error']}")
        sys.exit(1)

    result_json(True, message=f"Active explore sessions: {json.dumps(data, indent=2)}", extra={"sessions": data})
    sys.exit(0)

# ── ACTION: test ──
# Resolve content source and build explore URL
explore_url = ""

if artifact:
    # Find the artifact file
    artifact_path = find_artifact(artifact, project_id)
    if not artifact_path:
        result_json(False, message=f"Artifact '{artifact}' not found in {project_id} or any project folder")
        sys.exit(1)
    # Determine actual project from the found path
    parts = artifact_path.split("/")
    found_project = parts[2] if len(parts) >= 4 else project_id
    explore_url = (
        f"{DASHBOARD_URL}/explore"
        f"?source=artifact"
        f"&project_id={found_project}"
        f"&filename={urllib.request.quote(artifact)}"
        f"&device={urllib.request.quote(device)}"
    )

elif url:
    explore_url = (
        f"{DASHBOARD_URL}/explore"
        f"?source=url"
        f"&url={urllib.request.quote(url, safe='/:?=&#')}"
        f"&device={urllib.request.quote(device)}"
    )

elif file_path:
    # Read file and save as explore session artifact
    if not os.path.isfile(file_path):
        result_json(False, message=f"File not found: {file_path}")
        sys.exit(1)
    try:
        with open(file_path, "r", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        result_json(False, message=f"Cannot read file: {e}")
        sys.exit(1)

    session_id = str(uuid.uuid4())[:8]
    session_dir = os.path.join(EXPLORE_SESSIONS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    out_path = os.path.join(session_dir, "index.html")
    with open(out_path, "w") as f:
        f.write(content)

    explore_url = (
        f"{DASHBOARD_URL}/explore"
        f"?source=artifact"
        f"&project_id=explore-sessions"
        f"&filename={session_id}/index.html"
        f"&device={urllib.request.quote(device)}"
    )

elif text:
    # Wrap raw HTML in a basic page and save as explore session
    session_id = str(uuid.uuid4())[:8]
    session_dir = os.path.join(EXPLORE_SESSIONS_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    out_path = os.path.join(session_dir, "index.html")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>body{{font-family:system-ui,sans-serif;margin:0;padding:20px}}</style>
</head>
<body>
{text}
</body>
</html>"""
    with open(out_path, "w") as f:
        f.write(html)

    explore_url = (
        f"{DASHBOARD_URL}/explore"
        f"?source=artifact"
        f"&project_id=explore-sessions"
        f"&filename={session_id}/index.html"
        f"&device={urllib.request.quote(device)}"
    )

else:
    result_json(False, message="No content specified. Provide one of: artifact, url, file_path, or text")
    sys.exit(1)

result_json(True, explore_url=explore_url, device_name=device, message="Content ready for Explore Lab testing")
