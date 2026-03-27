#!/usr/bin/env python3
"""
Simon Bately — Dynamic Team Commander Briefing
Runs every 2 hours via cron. Pulls LIVE data on entire team state,
project progress, blockers, mining, crypto, weather — and tells Serge
exactly where the empire stands and what Simon is commanding the team to do.
"""
import os, sys, json, logging, sqlite3, subprocess, datetime, urllib.request
from pathlib import Path

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FRAMEWORK_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(FRAMEWORK_DIR, "configs", "secrets.env"))

from core.skills_engine import SkillsEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SIMON] %(message)s")
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_SIMON_BATELY", "8259565938:AAFCNLSrw096JALxvgmiBCkgByn0uDyGGMo")
SERGE_CHAT_ID  = os.getenv("SERGE_CHAT_ID", "8551331144")
OLLAMA_URL     = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL          = "mistral-small:22b"

AGENTS = [
    ("simon_bately",  "Simon",  "Co-CEO / BizOps"),
    ("claw_batto",    "Claw",   "Dev / DevOps"),
    ("phil_hass",     "Phil",   "Legal / Finance"),
    ("sam_axe",       "Sam",    "Design / Marketing"),
    ("duke_harmon",   "Duke",   "Project Manager"),
    ("rex_valor",     "Rex",    "Voicemail / Intake"),
    ("scout_reeves",  "Scout",  "Research"),
    ("nova_sterling", "Nova",   "Client Chat"),
]

# ── Data collection ────────────────────────────────────────────────────────────

def get_service_status(agent_id: str) -> str:
    svc = f"baza-agent-{agent_id.replace('_','-')}"
    try:
        r = subprocess.run(["systemctl","is-active", svc],
                           capture_output=True, text=True, timeout=3)
        return "🟢 online" if r.stdout.strip() == "active" else "🔴 offline"
    except:
        return "❓ unknown"

def get_team_status() -> str:
    lines = ["TEAM STATUS:"]
    for agent_id, name, role in AGENTS:
        status = get_service_status(agent_id)
        lines.append(f"  {name} ({role}): {status}")
    return "\n".join(lines)

def get_tasks_summary() -> str:
    """Fetch live tasks from local baza_projects.db SQLite."""
    db_candidates = [
        os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db"),
        os.path.join(FRAMEWORK_DIR, "baza_projects.db"),
    ]
    db_path = next((p for p in db_candidates if os.path.exists(p)), None)
    if not db_path:
        return "TASKS: local DB not found"

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT t.title, t.status, t.priority, t.assigned_to, t.notes,
                   t.updated_at, p.name as project_name
            FROM tasks t
            LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.is_subtask = 0
            ORDER BY t.updated_at DESC
            LIMIT 60
        """)
        tasks = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        log.warning(f"Local tasks DB failed: {e}")
        return "TASKS: DB read error"

    if not tasks:
        return "TASKS: no tasks found"

    by_status = {}
    for t in tasks:
        s = (t.get("status") or "pending").lower()
        by_status.setdefault(s, []).append(t)

    total   = len(tasks)
    done    = len(by_status.get("completed", []) + by_status.get("done", []))
    blocked = len(by_status.get("blocked", []))
    in_prog = len(by_status.get("in_progress", []))
    pending = len(by_status.get("pending", []))
    pct     = int(done / total * 100) if total else 0

    lines = [f"TASK BOARD: {total} tasks | {done} done ({pct}%) | {in_prog} active | {blocked} BLOCKED | {pending} pending"]

    if by_status.get("blocked"):
        lines.append("\n  🚫 BLOCKED — SERGE ACTION NEEDED:")
        for t in by_status["blocked"][:5]:
            proj = t.get("project_name") or "?"
            lines.append(f"    [{t.get('assigned_to','?')}] {t.get('title','?')[:70]} ({proj})")
            if t.get("notes"):
                lines.append(f"      → {t['notes'][:80]}")

    if by_status.get("in_progress"):
        lines.append("\n  🔄 IN PROGRESS:")
        for t in by_status["in_progress"][:6]:
            proj = t.get("project_name") or "?"
            lines.append(f"    [{t.get('assigned_to','?')}] {t.get('title','?')[:70]} ({proj})")

    high_pending = [t for t in by_status.get("pending", []) if t.get("priority") == "high"]
    if high_pending:
        lines.append("\n  ⏳ HIGH PRIORITY PENDING:")
        for t in high_pending[:4]:
            lines.append(f"    [{t.get('assigned_to','?')}] {t.get('title','?')[:70]}")

    return "\n".join(lines)

def get_recent_activity() -> str:
    """Read recent agent messages from SQLite context DB."""
    db_candidates = [
        os.path.join(FRAMEWORK_DIR, "data", "context.db"),
        os.path.join(FRAMEWORK_DIR, "context.db"),
    ]
    for db_path in db_candidates:
        if not os.path.exists(db_path): continue
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT agent_id, role, content, timestamp
                FROM messages
                WHERE role = 'assistant'
                ORDER BY timestamp DESC
                LIMIT 12
            """)
            rows = cur.fetchall()
            conn.close()
            if not rows: return "RECENT ACTIVITY: no messages found"
            lines = ["RECENT AGENT ACTIVITY:"]
            seen = set()
            for r in rows:
                agent = r["agent_id"]
                if agent in seen: continue
                seen.add(agent)
                content = (r["content"] or "")[:120].replace("\n"," ")
                ts = r["timestamp"] or ""
                lines.append(f"  {agent}: {content} [{ts[:16]}]")
            return "\n".join(lines)
        except Exception as e:
            continue
    return "RECENT ACTIVITY: context DB unavailable"

def get_artifacts_summary() -> str:
    """Count recent artifacts produced."""
    arts_dir = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")
    if not os.path.exists(arts_dir): return "ARTIFACTS: none"
    total = 0
    recent = []
    cutoff = datetime.datetime.now().timestamp() - 86400  # last 24h
    for proj in os.listdir(arts_dir):
        proj_dir = os.path.join(arts_dir, proj)
        if not os.path.isdir(proj_dir): continue
        for fname in os.listdir(proj_dir):
            fpath = os.path.join(proj_dir, fname)
            if os.path.isfile(fpath):
                total += 1
                if os.path.getmtime(fpath) > cutoff:
                    recent.append(f"{proj}/{fname}")
    lines = [f"ARTIFACTS: {total} total, {len(recent)} in last 24h"]
    for a in recent[:5]:
        lines.append(f"  {a}")
    return "\n".join(lines)

def get_mining_quick() -> str:
    try:
        req = urllib.request.Request("http://localhost:18080/2/summary",
                                      headers={"Authorization": "Bearer bazarig2024"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
            hr = data.get("hashrate",{}).get("total",[0])[0]
            return f"MINING: XMRig {hr:.1f} H/s active"
    except:
        pass
    # Check service
    try:
        r = subprocess.run(["systemctl","is-active","baza-mining"], capture_output=True, text=True, timeout=3)
        status = r.stdout.strip()
        return f"MINING: service {status}"
    except:
        return "MINING: status unavailable"

# ── LLM briefing ─────────────────────────────────────────────────────────────

def build_dynamic_briefing(live_data: str, team_status: str, tasks: str,
                            activity: str, artifacts: str, mining: str) -> str:
    now = datetime.datetime.now().strftime("%A, %B %d %Y — %I:%M %p")

    system = f"""You are Simon Bately — Co-CEO and Team Commander of the Baza Empire and AHBCO LLC.
You report directly to Serge (the boss). This is your scheduled 2-hour team command briefing.

STRICT FORMAT RULES — NO EXCEPTIONS:
- ZERO markdown. No #, ##, **, __, *, [], ()
- Use ━━━━━━━━━━━━━━━━ as section dividers
- Use emoji for labels and bullets only
- Plain text — no bold, no headers with pound signs
- Max 35 lines. Keep it sharp and actionable.
- Serge is the boss. Simon = commander. You command the team TO PLEASE SERGE.

YOU MUST COVER:
1. Empire pulse: who is online, who is offline right now
2. Active tasks: what is being worked on across all projects, by whom
3. What you (Simon) are commanding the team to do RIGHT NOW based on the task board
4. Any blockers or issues the team is hitting
5. Recent wins: what got done in the last 2 hours
6. Quick metrics: crypto, mining, weather (from live data)
7. Your flag: one urgent action item for Serge

TONE: You are a sharp, confident commander — not a reporter. You don't just describe what's happening. 
You tell Serge what you're DOING about it. "I've dispatched Claw to fix X", "I'm holding Phil to the deadline on Y", etc.
This is a real-time command report, not a status summary.

LIVE DATA:
{live_data}

{team_status}

{tasks}

{activity}

{artifacts}

{mining}
"""
    prompt = f"Send Serge his 2-hour command briefing for {now}. Be sharp. Own the room."

    try:
        payload = json.dumps({
            "model": MODEL, "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt}
            ]
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat", data=payload,
            headers={"Content-Type":"application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["message"]["content"].strip()
    except Exception as e:
        log.error(f"LLM error: {e}")
        return (
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 Simon Briefing — {now}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚠️ LLM unavailable: {e}\n\n"
            f"{team_status}\n\n"
            f"{tasks}"
        )

def strip_markdown(text: str) -> str:
    import re
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    return text.strip()

def send_telegram(text: str):
    text = strip_markdown(text)
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        payload = json.dumps({"chat_id": SERGE_CHAT_ID, "text": chunk}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=payload, headers={"Content-Type":"application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                result = json.loads(r.read())
                if result.get("ok"):
                    log.info("Briefing sent.")
                else:
                    log.error(f"Telegram error: {result}")
        except Exception as e:
            log.error(f"Telegram send failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("Simon 2-hour command briefing starting...")
    skills = SkillsEngine(FRAMEWORK_DIR)

    # Collect all live data in parallel where possible
    sections = {}

    # Skills data
    r = skills.run("crypto_prices", {"coins":["bitcoin","ethereum","monero","ravencoin","litecoin"]})
    sections["crypto"] = r.get("output","CRYPTO: unavailable") if r.get("success") else "CRYPTO: unavailable"

    r = skills.run("weather", {"location":"Philadelphia, PA"})
    sections["weather"] = r.get("output","WEATHER: unavailable") if r.get("success") else "WEATHER: unavailable"

    r = skills.run("mining_earnings", {})
    sections["mining_earnings"] = r.get("output","MINING EARNINGS: unavailable") if r.get("success") else "MINING EARNINGS: unavailable"

    r = skills.run("news", {"category":"crypto"})
    sections["news"] = r.get("output","NEWS: unavailable") if r.get("success") else "NEWS: unavailable"

    live_data = "\n\n".join([sections["crypto"], sections["weather"],
                              sections["mining_earnings"], sections["news"]])

    team_status  = get_team_status()
    tasks        = get_tasks_summary()
    activity     = get_recent_activity()
    artifacts    = get_artifacts_summary()
    mining_quick = get_mining_quick()

    log.info("All data collected. Building briefing...")
    briefing = build_dynamic_briefing(live_data, team_status, tasks, activity, artifacts, mining_quick)
    log.info(f"Briefing built ({len(briefing)} chars). Sending to Serge...")
    send_telegram(briefing)
    log.info("Done.")

if __name__ == "__main__":
    main()
