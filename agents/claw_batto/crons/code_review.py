#!/usr/bin/env python3
"""Claw Batto — Daily code review of recent git activity."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLAW-CODE] %(message)s")

MODEL = "mistral-small:22b"
AGENT_TOKEN = os.getenv("TELEGRAM_CLAW_BATTO", TELEGRAM_TOKEN)

def collect_data():
    git_log = run_cmd(f"cd {FRAMEWORK_DIR} && git log --oneline --since='24 hours ago' -20 2>/dev/null")
    git_diff_stat = run_cmd(f"cd {FRAMEWORK_DIR} && git diff --stat HEAD~5 2>/dev/null | tail -20")
    git_status = run_cmd(f"cd {FRAMEWORK_DIR} && git status --short 2>/dev/null | head -30")
    todos = run_cmd(f"cd {FRAMEWORK_DIR} && grep -rn 'TODO\\|FIXME\\|HACK\\|XXX' --include='*.py' core/ dashboard/app.py 2>/dev/null | head -15")
    return f"RECENT COMMITS (24h):\n{git_log or 'none'}\n\nDIFF STAT:\n{git_diff_stat or 'none'}\n\nUNTRACKED/MODIFIED:\n{git_status or 'clean'}\n\nTODOs/FIXMEs:\n{todos or 'none'}"

def main():
    log.info("Starting code review...")
    data = collect_data()
    if "none" in data and not run_cmd(f"cd {FRAMEWORK_DIR} && git log --oneline --since='24 hours ago' -1"):
        send_telegram("🔍 CODE REVIEW: No commits in last 24h. All quiet.", AGENT_TOKEN)
        return

    system = f"""You are Claw Batto — VP of Engineering reviewing the codebase.
Plain text only. Be terse. Focus on:
- Security concerns in recent changes
- Patterns that could cause bugs
- Outstanding TODOs that need attention
- Files with too many modifications (complexity risk)
Max 20 lines.

{data}"""
    report = ollama_generate(MODEL, system, f"Daily code review for {today()}")
    save_artifact("proj-baza-empire", f"code_review_{today()}.md", f"# Code Review — {today()}\n\n{report}")
    publish_event("claw_batto", "report_generated", {"type": "code_review", "summary": report[:200]})
    send_report("code_review", f"🔍 CODE REVIEW — {today()}\n\n{report}", priority="fyi", delta_key="code_review", token=AGENT_TOKEN)

if __name__ == "__main__":
    with cron_run("code_review"):
        main()
