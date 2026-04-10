#!/usr/bin/env python3
"""Format and publish a research report — save artifact, publish event, notify Serge."""
import os, sys, json, uuid, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
topic = args.get("topic", "Research Report")
findings = args.get("findings", "")
recommendations = args.get("recommendations", "")
agent_id = args.get("agent_id", "unknown")
sources = args.get("sources", [])

if not findings:
    print(json.dumps({"error": "findings are required"}))
    exit()

# Format report
report = f"""# {topic}
**Agent:** {agent_id}
**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}

## Findings
{findings}

## Recommendations
{recommendations}

## Sources
{chr(10).join('- ' + s for s in sources) if sources else 'Internal analysis'}
"""

# Save artifact
art_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../dashboard/artifacts/proj-research")
os.makedirs(art_dir, exist_ok=True)
slug = topic.lower().replace(" ", "_")[:40]
filename = f"{slug}_{datetime.date.today().isoformat()}.md"
filepath = os.path.join(art_dir, filename)
with open(filepath, "w") as f:
    f.write(report)

# Publish event
try:
    from core.event_bus import publish_sync
    publish_sync(agent_id, "research_complete", {
        "topic": topic, "artifact": f"proj-research/{filename}",
        "summary": findings[:200], "agent": agent_id
    })
except Exception:
    pass

# Notify via Telegram
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../configs/secrets.env"))
    import urllib.request
    token = os.getenv("TELEGRAM_SIMON_BATELY", "")
    chat_id = os.getenv("SERGE_CHAT_ID", "")
    if token and chat_id:
        msg = f"📋 Research Report: {topic}\nBy: {agent_id}\n\n{findings[:500]}\n\n💬 Was this sufficient? Reply with feedback or 'ok'."
        data = json.dumps({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
except Exception:
    pass

print(json.dumps({"artifact": f"proj-research/{filename}", "topic": topic, "status": "published"}))
