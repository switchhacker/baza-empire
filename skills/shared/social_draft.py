#!/usr/bin/env python3
"""Create a draft post in the Social Media Studio library (never publishes)."""
import json
import os
import sys
import urllib.request

SKILL_META = {
    "category": "marketing",
    "summary": "Save a social media post draft (caption + hashtags per platform) into the AHB123 Social Studio library for Serge to review, edit, and render.",
    "when_to_use": "when asked to draft a TikTok/Instagram post, write social content, or queue post ideas — the draft lands in the Social tab library, it is NOT published",
    "args": {
        "platform": "tiktok|ig_reel|ig_feed_square|ig_feed_portrait|ig_story (required)",
        "caption": "post caption text (required)",
        "hashtags": "space-separated #hashtags string, optional",
        "first_comment": "optional first-comment text",
        "project_id": "optional AHB123 project id to link",
    },
}

ALLOWED_PLATFORMS = {"tiktok", "ig_reel", "ig_feed_square", "ig_feed_portrait", "ig_story"}
DASHBOARD = os.environ.get("BAZA_DASHBOARD_URL", "http://localhost:8888")

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
platform = (args.get("platform") or "").strip()
caption = (args.get("caption") or "").strip()

if platform not in ALLOWED_PLATFORMS:
    print(json.dumps({"error": f"platform must be one of {sorted(ALLOWED_PLATFORMS)}"}))
    sys.exit(1)
if not caption:
    print(json.dumps({"error": "caption is required"}))
    sys.exit(1)

payload = {
    "platform": platform,
    "variant": "a",
    "status": "draft",
    "caption": caption,
    "hashtags": args.get("hashtags") or "",
    "ai_meta": {"drafted_by": os.environ.get("AGENT_ID", "unknown"), "via": "social_draft skill"},
}
if args.get("first_comment"):
    payload["first_comment"] = args["first_comment"]
if args.get("project_id"):
    payload["project_id"] = args["project_id"]

req = urllib.request.Request(
    DASHBOARD + "/api/ahb/social/posts",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        j = json.loads(r.read().decode())
except Exception as e:
    print(json.dumps({"error": f"dashboard unreachable: {e}"}))
    sys.exit(1)

if "id" in j:
    print(json.dumps({"ok": True, "post_id": j["id"], "status": "draft",
                      "note": "Draft saved to Social Studio library — Serge reviews/renders it there."}))
else:
    print(json.dumps({"error": j.get("error", "create failed")}))
    sys.exit(1)
