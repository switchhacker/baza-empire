#!/usr/bin/env python3
"""Claw Batto — 4-hourly infrastructure health check.
Checks services, GPUs, disk, DB, stale processes. Reports issues."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))
from agents.cron_helpers import *

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLAW-INFRA] %(message)s")

MODEL = "mistral-small:22b"
AGENT_TOKEN = os.getenv("TELEGRAM_CLAW_BATTO", TELEGRAM_TOKEN)

def collect_data():
    sections = []

    # Services
    services = ["baza-dashboard", "baza-tool-server", "postgresql", "redis-server", "nginx",
                "ollama-amd", "ollama-nvidia"]
    svc_lines = []
    for svc in services:
        status = run_cmd(f"systemctl is-active {svc} 2>/dev/null") or "not found"
        svc_lines.append(f"  {svc}: {status}")
    sections.append("SERVICES:\n" + "\n".join(svc_lines))

    # GPU status
    nvidia = run_cmd("nvidia-smi --query-gpu=name,temperature.gpu,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null")
    sections.append(f"NVIDIA GPU: {nvidia or 'unavailable'}")

    # Disk usage
    disk = run_cmd("df -h / /home --output=source,size,used,avail,pcent 2>/dev/null")
    sections.append(f"DISK:\n{disk}")

    # Memory
    mem = run_cmd("free -h | head -2")
    sections.append(f"MEMORY:\n{mem}")

    # Load
    load = run_cmd("uptime")
    sections.append(f"UPTIME/LOAD: {load}")

    # DB stats
    try:
        conn = get_db()
        tables = ["ahb_projects", "ahb_invoices", "ahb_receipts", "ahb_clients", "tasks"]
        db_lines = []
        for t in tables:
            try:
                cnt = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                db_lines.append(f"  {t}: {cnt} rows")
            except:
                pass
        conn.close()
        sections.append("DATABASE:\n" + "\n".join(db_lines))
    except Exception as e:
        sections.append(f"DATABASE: error — {e}")

    # Stale processes
    procs = run_cmd("ps aux --sort=-%mem | head -8")
    sections.append(f"TOP PROCESSES:\n{procs}")

    # Ollama models loaded
    ollama = run_cmd("curl -s http://localhost:11434/api/ps 2>/dev/null | python3 -m json.tool 2>/dev/null | head -20")
    sections.append(f"OLLAMA LOADED:\n{ollama or 'none'}")

    return "\n\n".join(sections)

def main():
    log.info("Starting infra health check...")
    data = collect_data()

    system = f"""You are Claw Batto — VP of Engineering & Infrastructure at AHBCO LLC.
You're running your scheduled infrastructure health check on the Baza server.

RULES:
- Plain text only, no markdown symbols
- Be terse and technical
- Flag any service that's down or degraded
- Flag disk usage over 80%
- Flag memory pressure
- Note any anomalies in process list
- If everything is green, say so briefly
- Max 25 lines

LIVE DATA:
{data}
"""
    report = ollama_generate(MODEL, system, f"Infrastructure health report for {now()}")

    # Save artifact
    filename = f"infra_health_{today()}.md"
    save_artifact("proj-baza-empire", filename, f"# Infra Health — {now()}\n\n{report}")

    # Publish event
    publish_event("claw_batto", "report_generated", {
        "type": "infra_health", "artifact": f"proj-baza-empire/{filename}", "summary": report[:200]
    })

    # Notify
    send_report("infra_health", f"🔧 INFRA HEALTH CHECK — {now()}\n\n{report}", priority="alert", token=AGENT_TOKEN)
    log.info("Done.")

if __name__ == "__main__":
    with cron_run("infra_health"):
        main()
