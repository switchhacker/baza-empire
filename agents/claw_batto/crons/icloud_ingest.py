#!/usr/bin/env python3
"""Claw Batto — periodic iCloud ingest cron.
Runs every 6h by default. Syncs every registered iCloud account (admin + cloud users),
classifies new photos as AHB jobsite vs personal, imports work photos into ahb_photos,
and sends Serge a Telegram summary."""
import os, sys, json, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
from agents.cron_helpers import send_telegram, save_artifact, log
from core.icloud_ingest import ingest_all, list_accounts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLAW-ICLOUD] %(message)s")


def fmt_summary(results):
    if not results:
        return "No iCloud accounts registered yet. Run /scripts/icloud_setup.py to add one."
    lines = ["<b>iCloud Ingest Summary</b>", ""]
    total_new, total_job, total_pers = 0, 0, 0
    for r in results:
        acct = r.get("account", "?")
        if not r.get("ok"):
            lines.append(f"❌ <b>{acct}</b>: {r.get('error','failed')}")
            if r.get("needs_2fa"):
                lines.append("    → re-auth needed: run scripts/icloud_setup.py")
            continue
        new = r.get("new_files", 0)
        job = r.get("jobsite", 0)
        per = r.get("personal", 0)
        total_new += new; total_job += job; total_pers += per
        lines.append(f"✅ <b>{acct}</b>")
        lines.append(f"    {new} new · 🏗 {job} jobsite · 📸 {per} personal")
        for s in r.get("samples", [])[:3]:
            lines.append(f"    • {s}")
    if total_new:
        lines.append("")
        lines.append(f"<b>Totals:</b> {total_new} new files · {total_job} jobsite · {total_pers} personal")
    return "\n".join(lines)


def main():
    log.info("iCloud ingest starting")
    accounts = list_accounts(user_id=None)
    log.info(f"found {len(accounts)} registered accounts")

    if not accounts:
        log.info("no accounts — nothing to do")
        return

    results = ingest_all(user_id=None)
    summary = fmt_summary(results)

    # Save artifact
    try:
        import datetime
        fname = f"icloud_ingest_{datetime.datetime.now().strftime('%Y-%m-%d_%H%M')}.md"
        save_artifact("proj-baza-empire", fname,
                      summary.replace("<b>","**").replace("</b>","**"))
    except Exception as e:
        log.warning(f"artifact save failed: {e}")

    # Telegram if anything new (or any error)
    has_changes = any(r.get("new_files", 0) > 0 or not r.get("ok") for r in results)
    if has_changes:
        try:
            send_telegram(summary)
        except Exception as e:
            log.warning(f"telegram failed: {e}")

    log.info(f"done: {json.dumps([{'a':r.get('account'),'n':r.get('new_files',0)} for r in results])}")


if __name__ == "__main__":
    main()
