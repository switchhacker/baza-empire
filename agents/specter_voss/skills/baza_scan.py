#!/usr/bin/env python3
"""
Specter Voss — Full Infrastructure Scan
Checks all Baza services, databases, GPUs, disk, and dashboard health.
"""
import os, json, subprocess, socket
from datetime import datetime

SKILL_ARGS = json.loads(os.environ.get("SKILL_ARGS", "{}"))
FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DB_CONFIG = {
    "host": os.environ.get("BAZA_DB_HOST", "localhost"),
    "port": int(os.environ.get("BAZA_DB_PORT", "5432")),
    "dbname": os.environ.get("BAZA_DB_NAME", "baza_agents"),
    "user": os.environ.get("BAZA_DB_USER", "switchhacker"),
    "password": os.environ.get("DB_PASSWORD", "baza2026"),
}


def run_cmd(cmd, timeout=10):
    """Run a shell command and return stdout or error string."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=isinstance(cmd, str))
        return r.stdout.strip() if r.returncode == 0 else f"ERROR: {r.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out"
    except Exception as e:
        return f"ERROR: {e}"


def check_port(host, port, timeout=3):
    """Check if a TCP port is reachable."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def check_systemd_services():
    """Check all baza-* systemd services."""
    output = run_cmd("systemctl list-units 'baza-*' --no-pager --no-legend")
    if output.startswith("ERROR"):
        # Fallback: check known services individually
        services = [
            "baza-agents", "baza-dashboard", "baza-task-runner",
            "baza-tool-server", "baza-litellm",
        ]
        lines = []
        for svc in services:
            status = run_cmd(f"systemctl is-active {svc}.service 2>/dev/null")
            lines.append(f"  {svc}: {status}")
        return "\n".join(lines) if lines else "  No baza-* services found"
    if not output:
        # Try timer units too
        output = run_cmd("systemctl list-units 'baza-*' --type=service --type=timer --no-pager --no-legend")
    return output if output else "  No baza-* units found"


def check_postgres():
    """Check PostgreSQL connectivity and key table row counts."""
    try:
        import psycopg2
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        tables = [
            "agent_memory", "agent_summaries", "empire_knowledge",
            "agent_skills", "task_journal", "agent_identity",
        ]
        lines = ["  Connected to PostgreSQL"]
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                lines.append(f"  {table}: {count} rows")
            except Exception as e:
                conn.rollback()
                lines.append(f"  {table}: not found ({e})")
        cur.close()
        conn.close()
        return "\n".join(lines)
    except ImportError:
        return "  ERROR: psycopg2 not installed"
    except Exception as e:
        return f"  ERROR: {e}"


def check_redis():
    """Check Redis connectivity and key counts."""
    try:
        import redis
        r = redis.Redis(
            host=os.environ.get("BAZA_REDIS_HOST", "localhost"),
            port=int(os.environ.get("BAZA_REDIS_PORT", "6379")),
            decode_responses=True,
        )
        r.ping()
        total_keys = r.dbsize()
        heartbeat_keys = len(r.keys("baza:heartbeat:*"))
        chat_keys = len(r.keys("chat:*"))
        return f"  Connected | Total keys: {total_keys} | Heartbeats: {heartbeat_keys} | Chat histories: {chat_keys}"
    except ImportError:
        return "  ERROR: redis module not installed"
    except Exception as e:
        return f"  ERROR: {e}"


def check_ollama():
    """Check both Ollama GPU instances."""
    lines = []
    for port, label in [(11434, "AMD Vulkan"), (11435, "NVIDIA CUDA")]:
        reachable = check_port("localhost", port)
        if reachable:
            models = run_cmd(f"curl -sf http://localhost:{port}/api/tags 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(', '.join(m['name'] for m in d.get('models',[])))\" 2>/dev/null")
            lines.append(f"  :{port} ({label}): UP | Models: {models if models and not models.startswith('ERROR') else 'unable to list'}")
        else:
            lines.append(f"  :{port} ({label}): DOWN")
    return "\n".join(lines)


def check_disk():
    """Check disk usage on key paths."""
    paths = ["/", FRAMEWORK_DIR, os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")]
    lines = []
    for p in paths:
        if os.path.exists(p):
            result = run_cmd(f"df -h '{p}' | tail -1 | awk '{{print $4 \" free (\" $5 \" used)\"}}'")
            lines.append(f"  {p}: {result}")
    # Framework dir size
    fw_size = run_cmd(f"du -sh '{FRAMEWORK_DIR}' 2>/dev/null | cut -f1")
    lines.append(f"  Framework size: {fw_size}")
    return "\n".join(lines)


def check_dashboard():
    """Check dashboard health."""
    reachable = check_port("localhost", 8888)
    if reachable:
        resp = run_cmd("curl -sf -o /dev/null -w '%{http_code}' http://localhost:8888/ 2>/dev/null")
        return f"  :8888 UP | HTTP {resp}"
    return "  :8888 DOWN"


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== BAZA INFRASTRUCTURE SCAN ===")
    print(f"Timestamp: {ts}")
    print()

    print("[SYSTEMD SERVICES]")
    print(check_systemd_services())
    print()

    print("[POSTGRESQL]")
    print(check_postgres())
    print()

    print("[REDIS]")
    print(check_redis())
    print()

    print("[OLLAMA GPU POOL]")
    print(check_ollama())
    print()

    print("[DISK USAGE]")
    print(check_disk())
    print()

    print("[DASHBOARD]")
    print(check_dashboard())
    print()

    # Tool server
    print("[TOOL SERVER]")
    ts_up = check_port("localhost", 8000)
    print(f"  :8000 {'UP' if ts_up else 'DOWN'}")

    # LiteLLM proxy
    print()
    print("[LITELLM PROXY]")
    ll_up = check_port("localhost", 4000)
    print(f"  :4000 {'UP' if ll_up else 'DOWN'}")

    print()
    print("=== SCAN COMPLETE ===")


if __name__ == "__main__":
    main()
