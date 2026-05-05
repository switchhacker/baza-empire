#!/usr/bin/env python3
"""
Weekly Hallucination Digest — Sunday evening Telegram report.

Rolls up the last 7 days of:
  - per-agent claims (task_journal results containing completion verbs)
  - per-agent ships (artifacts saved attributed to that agent)
  - auto-corrected hallucinations (dispatch_sent events with
    trigger=claim_verifier in task_events)
  - outstanding drift (talk - ship gap that's still open)

Sends to Serge via Simon's Telegram bot. Disable per run with
BAZA_HALLUCINATION_DIGEST_DISABLED=1.

Recommended cron: 0 18 * * 0   (Sundays 6pm)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, "configs", "secrets.env"))

SIMON_TOKEN = os.environ.get("TELEGRAM_SIMON_BATELY")
SERGE_CHAT = os.environ.get("SERGE_CHAT_ID")


def fetch_pulse(days: int = 7) -> dict:
    """Reuse the dashboard endpoint via test_client so we get exactly the
    same numbers the user sees."""
    sys.path.insert(0, ROOT)
    from dashboard import app as appmod
    c = appmod.app.test_client()
    r = c.get(f"/api/empire-pulse?days={days}")
    return r.get_json() or {}


def auto_correction_count() -> dict[str, int]:
    """Count dispatch_sent events with trigger=claim_verifier in last 7d."""
    from core import task_events as te
    events = te.list_events(kinds=["dispatch_sent"], limit=500)
    out: dict[str, int] = {}
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 86400
    for e in events:
        try:
            ts = datetime.fromisoformat(e["ts"].replace(" ", "T"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts.timestamp() < cutoff:
                continue
        except Exception:
            continue
        payload = e.get("payload") or {}
        if payload.get("trigger") != "claim_verifier":
            continue
        ag = e.get("agent_id") or "_unknown"
        out[ag] = out.get(ag, 0) + 1
    return out


def render(pulse: dict, auto_corrections: dict[str, int]) -> str:
    now = datetime.now().strftime("%A, %B %d %Y")
    agents = pulse.get("agents") or []
    totals = pulse.get("totals") or {}
    if not agents:
        return f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 Empire Pulse — Weekly Digest\n{now}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\nNo agent activity in the last 7 days."

    # Sort: top drifters first, then by claims volume
    drifters = sorted(agents, key=lambda a: -a.get("drift_score", 0))[:3]
    flagged_ids = {a["agent_id"] for a in drifters if a.get("drift_score", 0) >= 30}

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Empire Pulse — Weekly Digest",
        f"   {now}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🟢 Shipping:   {totals.get('shipping', 0)} agents",
        f"🟡 Drifting:   {totals.get('drifting', 0)} agents",
        f"🔴 All-Talk:   {totals.get('all_talk', 0)} agents",
        f"📦 Tracked:    {totals.get('agents', 0)} agents over 7 days",
        "",
    ]

    if flagged_ids:
        lines.append("⚠️ TOP DRIFTERS (claim ≫ ship):")
        for a in drifters:
            if a["agent_id"] not in flagged_ids:
                continue
            lines.append(
                f"   • {a['agent_id']:18s}  drift {a['drift_score']:.1f}  "
                f"({a['talked_about_completing']} claims / {a['shipped']} ships)"
            )
        lines.append("")

    # Auto-corrections summary
    total_auto = sum(auto_corrections.values())
    if total_auto:
        lines.append(f"🔁 Auto-corrected this week: {total_auto} hallucination DISPATCH(es)")
        for ag, n in sorted(auto_corrections.items(), key=lambda kv: -kv[1]):
            lines.append(f"   • {ag}: {n}")
        lines.append("")
    else:
        lines.append("🔁 Auto-corrections: none triggered this week")
        lines.append("")

    # Per-agent table
    lines.append("📈 PER-AGENT:")
    for a in sorted(agents, key=lambda x: ({"red": 0, "yellow": 1, "green": 2}[x["health"]], -x.get("drift_score", 0))):
        emoji = {"red": "🔴", "yellow": "🟡", "green": "🟢"}[a["health"]]
        ratio_pct = int(round(a["ratio"] * 100))
        lines.append(
            f"   {emoji} {a['agent_id']:18s}  ship/talk {ratio_pct:3d}%  "
            f"(s:{a['shipped']:3d} t:{a['talked_about_completing']:3d})"
        )

    # Outstanding drift = sum of (talk - ship) only where positive
    outstanding = sum(
        max(0, a["talked_about_completing"] - a["shipped"]) for a in agents
    )
    if outstanding:
        lines.append("")
        lines.append(f"📌 Outstanding drift (open gap): {outstanding} claim(s) "
                     f"without matching ship — review during week-start planning.")

    lines.append("")
    lines.append("Detail: http://localhost:8888/empire-pulse")
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    if not SIMON_TOKEN or not SERGE_CHAT:
        print("[hallucination_digest] missing Telegram env — printing instead:")
        print(text)
        return False
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [text]
    ok = True
    for chunk in chunks:
        payload = json.dumps({
            "chat_id": SERGE_CHAT, "text": chunk, "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{SIMON_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                if not (json.loads(r.read()).get("ok")):
                    ok = False
        except Exception as e:
            print(f"[hallucination_digest] send error: {e}")
            ok = False
    return ok


def main() -> int:
    if os.environ.get("BAZA_HALLUCINATION_DIGEST_DISABLED", "0") in ("1", "true", "yes"):
        print("[hallucination_digest] disabled via env")
        return 0
    pulse = fetch_pulse(days=7)
    if pulse.get("error"):
        print(f"[hallucination_digest] pulse error: {pulse['error']}")
        return 1
    autos = auto_correction_count()
    text = render(pulse, autos)
    sent = send_telegram(text)
    print(f"[hallucination_digest] sent={sent}, length={len(text)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
