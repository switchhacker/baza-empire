#!/usr/bin/env python3
"""Scout Reeves — Daily market research: construction trends, material prices, permits."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SCOUT-MARKET] %(message)s")

MODEL = "claude-3-5-haiku"  # Scout uses cloud model for research quality
AGENT_TOKEN = os.getenv("TELEGRAM_SCOUT_REEVES", TELEGRAM_TOKEN)

def collect_data():
    conn = get_db()
    # Current project types for context
    proj_types = conn.execute("SELECT DISTINCT description FROM ahb_projects WHERE status IN ('In Progress','Planning') LIMIT 5").fetchall()
    proj_locations = conn.execute("SELECT DISTINCT location FROM ahb_projects WHERE location != '' LIMIT 5").fetchall()
    conn.close()

    data = f"""MARKET WATCH CONTEXT — {today()}

COMPANY: All Home Building Co LLC (AHBCO), Philadelphia PA
LICENSE: PA HIC licensed residential general contractor
SERVICE AREA: Bensalem, Philadelphia, Bucks County PA

ACTIVE PROJECT TYPES:
{chr(10).join('  ' + (p[0] or 'general')[:80] for p in proj_types) if proj_types else '  General residential'}

PROJECT LOCATIONS:
{chr(10).join('  ' + p[0] for p in proj_locations) if proj_locations else '  Greater Philadelphia area'}

RESEARCH AREAS:
- Philadelphia/Bucks County construction market trends
- Material pricing changes (lumber, concrete, electrical, plumbing)
- PA permit requirements and regulation changes
- Competitor activity in the area
- Seasonal demand patterns
"""
    return data

def main():
    log.info("Starting market watch...")
    data = collect_data()
    system = f"""You are Scout Reeves — Director of Research & Market Intelligence at AHBCO LLC.
Daily market intelligence brief for Serge. Plain text, no markdown. Max 25 lines.

Based on your knowledge of the Philadelphia construction market:
- Note any seasonal trends relevant to current month
- Material price trends
- Regulatory changes in PA affecting contractors
- Business opportunities in the service area
- Competitive landscape observations

This is analysis from your expertise — not web search results.
Be specific and actionable. What should AHBCO do differently this week?

{data}"""

    # Try cloud model for quality research
    try:
        import urllib.request
        payload = json.dumps({
            "model": MODEL, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Daily market intelligence brief for {today()}"}
            ], "max_tokens": 800, "temperature": 0.6
        }).encode()
        litellm_key = os.getenv("LITELLM_MASTER_KEY", "baza-litellm-internal")
        req = urllib.request.Request("http://localhost:4000/v1/chat/completions",
                                     data=payload, headers={"Content-Type": "application/json",
                                                            "Authorization": f"Bearer {litellm_key}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            report = json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception:
        report = ollama_generate("qwen2.5:14b", system, f"Daily market brief for {today()}")

    save_artifact("proj-research", f"market_watch_{today()}.md", f"# Market Watch — {today()}\n\n{report}")
    publish_event("scout_reeves", "research_complete", {"topic": "daily_market_watch", "summary": report[:200]})
    send_report("market_watch", f"🔍 MARKET WATCH — {today()}\n\n{report}\n\n💬 Was this sufficient? Reply with feedback or 'ok'.", priority="fyi", delta_key="market_watch", token=AGENT_TOKEN)

if __name__ == "__main__":
    with cron_run("market_watch"):
        main()
