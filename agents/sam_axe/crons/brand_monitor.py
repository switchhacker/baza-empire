#!/usr/bin/env python3
"""Sam Axe — Daily brand & marketing review."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SAM-BRAND] %(message)s")

MODEL = "qwen3-vl:latest"
AGENT_TOKEN = os.getenv("TELEGRAM_SAM_AXE", TELEGRAM_TOKEN)

def collect_data():
    # Recent artifacts (checking for brand/marketing content)
    arts_dir = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")
    recent_arts = []
    cutoff = datetime.datetime.now().timestamp() - 86400 * 7  # last 7 days
    for proj in os.listdir(arts_dir) if os.path.exists(arts_dir) else []:
        proj_dir = os.path.join(arts_dir, proj)
        if not os.path.isdir(proj_dir):
            continue
        for fname in os.listdir(proj_dir):
            fpath = os.path.join(proj_dir, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) > cutoff:
                recent_arts.append(f"{proj}/{fname}")

    # Project photos count
    conn = get_db()
    photo_count = conn.execute("SELECT count(*) FROM ahb_files WHERE photo_section != ''").fetchone()[0]
    project_count = conn.execute("SELECT count(*) FROM ahb_projects").fetchone()[0]
    client_count = conn.execute("SELECT count(*) FROM ahb_clients").fetchone()[0]
    conn.close()

    return f"""BRAND & MARKETING DATA — {today()}

RECENT ARTIFACTS (7 days): {len(recent_arts)}
{chr(10).join('  ' + a for a in recent_arts[:10]) if recent_arts else '  None'}

PORTFOLIO:
  Projects: {project_count}
  Project photos: {photo_count}
  Clients: {client_count}

BRAND ASSETS:
  Logo: dashboard/static/img/ahb_logo.jpeg
  Website: ahb123.com (Squarespace — migration planned)
  Dashboard: http://100.127.118.103:8888/ahb123

MARKETING CHANNELS:
  Website (ahb123.com), Google Business, Yelp, social media (status unknown)
"""

def main():
    log.info("Starting brand monitor...")
    data = collect_data()
    system = f"""You are Sam Axe — VP of Creative & Marketing at AHBCO LLC.
Daily brand and marketing review. Plain text, no markdown. Max 20 lines.
Review what content was produced, suggest marketing actions, check brand consistency.
Think about: portfolio updates, client testimonials, social posts, SEO.

{data}"""
    report = ollama_generate(MODEL, system, f"Daily brand & marketing review for {today()}")
    save_artifact("proj-ahb123", f"brand_monitor_{today()}.md", f"# Brand Monitor — {today()}\n\n{report}")
    send_report("brand_monitor", f"🎨 BRAND MONITOR — {today()}\n\n{report}", priority="fyi", delta_key="brand_monitor", token=AGENT_TOKEN)

if __name__ == "__main__":
    with cron_run("brand_monitor"):
        main()
