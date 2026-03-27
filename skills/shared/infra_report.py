#!/usr/bin/env python3
"""
Baza Empire — Daily Infrastructure Report
Run by Claw Batto via systemd cron.
Reads live system state, generates a markdown report, saves to artifacts,
and sends to Serge via Telegram.

Schedule: daily at 8am (set in claw's cron or systemd timer)
Run: python infra_report.py
"""
import os, sys, json, subprocess, datetime, socket, urllib.request

TELEGRAM_TOKEN = os.environ.get("SIMON_TELEGRAM_TOKEN", "8259565938:AAFCNLSrw096JALxvgmiBCkgByn0uDyGGMo")
SERGE_CHAT_ID  = os.environ.get("SERGE_CHAT_ID", "8551331144")
FRAMEWORK_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS_DIR  = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts", "proj-baza-empire")
REPORT_LOG     = os.path.join(FRAMEWORK_DIR, "logs", "infra_report.log")

os.makedirs(ARTIFACTS_DIR, exist_ok=True)
os.makedirs(os.path.dirname(REPORT_LOG), exist_ok=True)

TODAY = datetime.date.today().isoformat()
NOW   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ── Helpers ──────────────────────────────────────────────────────────────────

def run_cmd(cmd: str, timeout: int = 5) -> str:
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, timeout=timeout)
        return out.decode("utf-8", errors="ignore").strip()
    except Exception:
        return "unavailable"

def check_service(name: str) -> str:
    out = run_cmd(f"systemctl is-active {name}")
    return "✅ running" if out == "active" else f"❌ {out}"

def check_port(host: str, port: int, label: str) -> str:
    import socket
    try:
        with socket.create_connection((host, port), timeout=2):
            return f"✅ {label}:{port} up"
    except Exception:
        return f"❌ {label}:{port} down"

def ollama_models() -> str:
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            models = [m["name"] for m in data.get("models", [])]
            return ", ".join(models) if models else "none loaded"
    except Exception:
        return "unavailable"

def xmrig_hashrate() -> str:
    try:
        req = urllib.request.Request("http://localhost:18080/2/summary",
                                      headers={"Authorization": "Bearer bazarig2024"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
            hr = data.get("hashrate", {}).get("total", [0])[0]
            return f"{hr:.1f} H/s"
    except Exception:
        return "unavailable"

def disk_usage() -> str:
    return run_cmd("df -h / | tail -1 | awk '{print $3\"/\"$2\" used (\"$5\")\"}'")

def mem_usage() -> str:
    return run_cmd("free -h | awk '/^Mem:/{print $3\"/\"$2\" used\"}'")

def cpu_temp() -> str:
    out = run_cmd("sensors 2>/dev/null | grep -i 'Package id 0\\|Tdie\\|CPU Temp' | head -1 | awk '{print $3,$4}'")
    return out if out and out != "unavailable" else run_cmd("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf \"%.1f°C\", $1/1000}'")

def uptime_str() -> str:
    return run_cmd("uptime -p")

def gpu_status() -> str:
    nvidia = run_cmd("nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,power.draw --format=csv,noheader,nounits 2>/dev/null | head -1")
    amd    = run_cmd("rocm-smi --showtemp --showuse 2>/dev/null | grep -v '==\\|GPU\\|^$' | head -3")
    out = []
    if nvidia and nvidia != "unavailable":
        parts = [p.strip() for p in nvidia.split(",")]
        if len(parts) >= 4:
            out.append(f"NVIDIA {parts[0]}: {parts[1]}°C | {parts[2]}% util | {parts[3]}W")
    if amd and amd != "unavailable":
        out.append(f"AMD: {amd[:80]}")
    return "\n".join(out) if out else "no GPU data"

def agent_statuses() -> str:
    agents = ["simon_bately","claw_batto","sam_axe","phil_hass",
              "duke_harmon","rex_valor","scout_reeves","nova_sterling"]
    lines = []
    for a in agents:
        svc = f"baza-agent-{a.replace('_','-')}"
        lines.append(f"  {a}: {check_service(svc)}")
    return "\n".join(lines)

def send_telegram(msg: str):
    payload = json.dumps({"chat_id": SERGE_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}", file=sys.stderr)
        return False

# ── Build report ─────────────────────────────────────────────────────────────

def build_report() -> str:
    hostname = socket.gethostname()
    report = f"""# Baza Empire — Daily Infrastructure Report
**Date:** {NOW}
**Host:** {hostname}
**Uptime:** {uptime_str()}

---

## System Health
- CPU Temp: {cpu_temp()}
- Memory: {mem_usage()}
- Disk (/): {disk_usage()}

## GPU Status
{gpu_status()}

## Mining
- XMRig (NUC): {check_service('baza-nuc-mining')}
- XMRig hashrate: {xmrig_hashrate()}
- baza-mining service: {check_service('baza-mining')}

## Services
- Ollama: {check_port('localhost', 11434, 'ollama')} — Models: {ollama_models()}
- Dashboard: {check_port('localhost', 8888, 'dashboard')}
- SD WebUI: {check_port('localhost', 7860, 'sd-webui')}
- Mosquitto: {check_service('mosquitto')}
- Nextcloud: {check_service('nextcloud')}
- PostgreSQL: {check_service('postgresql')}
- Docker: {check_service('docker')}

## Agents
{agent_statuses()}

---
*Generated by Claw Batto | Baza Empire | {NOW}*
"""
    return report


def build_telegram_msg(report: str) -> str:
    # Extract key lines for a compact Telegram message
    lines = []
    for line in report.split("\n"):
        if any(k in line for k in ["CPU Temp","Memory","Disk","XMRig","Ollama","Dashboard",
                                    "✅","❌","simon","claw","phil","sam","duke","rex","scout","nova"]):
            lines.append(line.strip())
    body = "\n".join(lines[:30])
    return (
        f"<b>🔧 Baza Daily Infra Report</b>\n"
        f"<i>{NOW}</i>\n\n"
        f"<pre>{body}</pre>\n\n"
        f"Full report saved to Dashboard > Artifacts > proj-baza-empire"
    )


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[infra_report] Building report at {NOW}")

    report   = build_report()
    filename = f"infra_report_{TODAY}.md"
    fpath    = os.path.join(ARTIFACTS_DIR, filename)

    with open(fpath, "w") as f:
        f.write(report)
    print(f"[infra_report] Saved: {fpath}")

    tg_msg = build_telegram_msg(report)
    ok = send_telegram(tg_msg)
    print(f"[infra_report] Telegram: {'sent' if ok else 'FAILED'}")

    with open(REPORT_LOG, "a") as f:
        f.write(f"{NOW} | {'OK' if ok else 'FAIL'} | {filename}\n")
