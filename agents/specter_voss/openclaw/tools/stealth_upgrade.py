#!/usr/bin/env python3
"""
Stealth Upgrade Engine — Executes approved upgrades on the Baza infrastructure.
All operations go through the approval gate before execution.

Upgrade types:
  - deploy_code:    git pull + restart services on main server
  - deploy_skill:   push a new skill to shared skills
  - update_config:  modify agent configs, cron jobs, env vars
  - restart_service: restart a systemd service
  - install_package: install a pip/apt package
  - run_migration:  run a database migration
  - custom_script:  run an arbitrary approved script

Usage:
    SKILL_ARGS='{"type":"deploy_skill","name":"new_skill","code":"..."}' python3 stealth_upgrade.py
"""
import os
import sys
import json
import subprocess
import socket
import time
from datetime import datetime

# Add parent paths
sys.path.insert(0, os.path.dirname(__file__))
from approval_gate import request_approval, send_telegram

# Force IPv6
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET6] or _orig(*a, **k)

# Main server connection (via Tailscale SSH)
MAIN_SERVER = os.environ.get("BAZA_MAIN_HOST", "baza-main")
MAIN_USER = os.environ.get("BAZA_MAIN_USER", "switchhacker")
BAZA_DIR = "/home/switchhacker/baza-empire/agent-framework-v3"
VENV_PYTHON = f"{BAZA_DIR}/venv/bin/python"


def ssh_exec(cmd: str, timeout: int = 60) -> dict:
    """Execute a command on the main server via SSH."""
    full_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                f"{MAIN_USER}@{MAIN_SERVER}", cmd]
    try:
        proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "success": proc.returncode == 0,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "SSH command timed out", "returncode": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}


def scp_to_main(local_path: str, remote_path: str) -> dict:
    """Copy a file to the main server via SCP."""
    full_cmd = ["scp", "-o", "StrictHostKeyChecking=no",
                local_path, f"{MAIN_USER}@{MAIN_SERVER}:{remote_path}"]
    try:
        proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
        return {"success": proc.returncode == 0, "stderr": proc.stderr.strip()}
    except Exception as e:
        return {"success": False, "stderr": str(e)}


def log_upgrade(upgrade_type: str, details: str, success: bool, output: str = ""):
    """Log upgrade to local file and send summary to Telegram."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "SUCCESS" if success else "FAILED"
    log_line = f"[{timestamp}] [{status}] {upgrade_type}: {details}\n"

    log_dir = os.path.expanduser("~/.specter/upgrade_logs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "upgrades.log"), "a") as f:
        f.write(log_line)
        if output:
            f.write(f"  Output: {output[:500]}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# Upgrade Operations
# ═══════════════════════════════════════════════════════════════════════════════

def deploy_code(branch: str = "main") -> dict:
    """Pull latest code and restart agents on main server."""
    details = f"git pull origin {branch} + restart agent services"

    if not request_approval("Deploy Code Update", details, category="deploy"):
        return {"success": False, "reason": "denied"}

    # Pull code
    result = ssh_exec(f"cd {BAZA_DIR} && git pull origin {branch}")
    if not result["success"]:
        log_upgrade("deploy_code", details, False, result["stderr"])
        send_telegram(f"Deploy FAILED: {result['stderr'][:300]}")
        return result

    # Restart agent services
    restart_result = ssh_exec(
        "sudo systemctl restart baza-agent-*.service baza-dashboard.service"
    )

    success = restart_result["success"]
    output = f"Pull: {result['stdout'][:200]}\nRestart: {'OK' if success else restart_result['stderr'][:200]}"
    log_upgrade("deploy_code", details, success, output)
    send_telegram(f"Deploy {'completed' if success else 'FAILED'}:\n<code>{output[:500]}</code>")
    return {"success": success, "output": output}


def deploy_skill(name: str, code: str, description: str = "", agent_specific: str = "") -> dict:
    """Deploy a new skill to the main server."""
    target = f"agents/{agent_specific}/skills/{name}.py" if agent_specific else f"skills/shared/{name}.py"
    details = f"New skill: {name}\nTarget: {target}\nDescription: {description}\nCode length: {len(code)} chars"

    if not request_approval(f"Deploy Skill: {name}", details, category="skill"):
        return {"success": False, "reason": "denied"}

    # Write skill locally first
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    tmp.write(code)
    tmp.close()

    # SCP to main server
    remote_path = f"{BAZA_DIR}/{target}"
    result = scp_to_main(tmp.name, remote_path)
    os.unlink(tmp.name)

    if result["success"]:
        # Make executable
        ssh_exec(f"chmod +x {remote_path}")
        log_upgrade("deploy_skill", f"{name} -> {target}", True)
        send_telegram(f"Skill <b>{name}</b> deployed to <code>{target}</code>")
    else:
        log_upgrade("deploy_skill", f"{name} -> {target}", False, result["stderr"])
        send_telegram(f"Skill deploy FAILED: {result['stderr'][:300]}")

    return result


def update_config(file_path: str, content: str, backup: bool = True) -> dict:
    """Update a config file on the main server."""
    details = f"File: {file_path}\nContent length: {len(content)} chars\nBackup: {backup}"

    if not request_approval(f"Update Config: {file_path}", details, category="config"):
        return {"success": False, "reason": "denied"}

    # Create backup
    if backup:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ssh_exec(f"cp {BAZA_DIR}/{file_path} {BAZA_DIR}/{file_path}.bak.{timestamp}")

    # Write new config
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode='w', delete=False)
    tmp.write(content)
    tmp.close()

    result = scp_to_main(tmp.name, f"{BAZA_DIR}/{file_path}")
    os.unlink(tmp.name)

    if result["success"]:
        log_upgrade("update_config", file_path, True)
        send_telegram(f"Config updated: <code>{file_path}</code>")
    else:
        log_upgrade("update_config", file_path, False, result["stderr"])

    return result


def restart_service(service_name: str) -> dict:
    """Restart a systemd service on the main server."""
    # Validate service name to prevent injection
    if not service_name.startswith("baza-"):
        return {"success": False, "reason": f"Only baza-* services allowed, got: {service_name}"}

    details = f"Restart {service_name}"

    if not request_approval(f"Restart Service: {service_name}", details, category="restart"):
        return {"success": False, "reason": "denied"}

    result = ssh_exec(f"sudo systemctl restart {service_name}")

    if result["success"]:
        # Check status
        status = ssh_exec(f"systemctl is-active {service_name}")
        active = status["stdout"] == "active"
        log_upgrade("restart_service", service_name, active, status["stdout"])
        send_telegram(f"Service <b>{service_name}</b>: {'active' if active else 'FAILED'}")
        return {"success": active, "status": status["stdout"]}
    else:
        log_upgrade("restart_service", service_name, False, result["stderr"])
        send_telegram(f"Restart FAILED: {result['stderr'][:300]}")
        return result


def install_package(package: str, manager: str = "pip") -> dict:
    """Install a package on the main server."""
    if manager == "pip":
        cmd = f"cd {BAZA_DIR} && ./venv/bin/pip install {package}"
    elif manager == "apt":
        cmd = f"sudo apt install -y {package}"
    elif manager == "npm":
        cmd = f"npm install -g {package}"
    else:
        return {"success": False, "reason": f"Unknown package manager: {manager}"}

    details = f"{manager} install {package}"

    if not request_approval(f"Install Package: {package}", details, category="install"):
        return {"success": False, "reason": "denied"}

    result = ssh_exec(cmd, timeout=120)
    log_upgrade("install_package", details, result["success"], result["stdout"][:300])

    if result["success"]:
        send_telegram(f"Installed <b>{package}</b> via {manager}")
    else:
        send_telegram(f"Install FAILED: {result['stderr'][:300]}")

    return result


def run_migration(sql: str = "", script: str = "") -> dict:
    """Run a database migration on the main server."""
    if sql:
        details = f"SQL: {sql[:200]}"
        cmd = f'cd {BAZA_DIR} && PGPASSWORD="${{DB_PASSWORD}}" psql -h localhost -U switchhacker -d baza_agents -c "{sql}"'
    elif script:
        details = f"Script: {script}"
        cmd = f"cd {BAZA_DIR} && {VENV_PYTHON} {script}"
    else:
        return {"success": False, "reason": "No SQL or script provided"}

    if not request_approval("Database Migration", details, category="migration"):
        return {"success": False, "reason": "denied"}

    # Always backup first
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ssh_exec(f'PGPASSWORD="${{DB_PASSWORD}}" pg_dump -h localhost -U switchhacker baza_agents > /tmp/baza_backup_{timestamp}.sql')

    result = ssh_exec(cmd, timeout=120)
    log_upgrade("run_migration", details, result["success"], result["stdout"][:300])

    if result["success"]:
        send_telegram(f"Migration complete:\n<code>{result['stdout'][:300]}</code>")
    else:
        send_telegram(f"Migration FAILED:\n<code>{result['stderr'][:300]}</code>")

    return result


def custom_script(script_content: str, description: str = "") -> dict:
    """Execute a custom script on the main server (highest risk — always requires approval)."""
    details = f"Description: {description}\nScript:\n{script_content[:500]}"

    # Custom scripts NEVER auto-approve
    if not request_approval(f"Run Custom Script: {description}", details, category=""):
        return {"success": False, "reason": "denied"}

    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False)
    tmp.write("#!/bin/bash\nset -euo pipefail\n" + script_content)
    tmp.close()

    remote_path = f"/tmp/specter_script_{int(time.time())}.sh"
    scp_result = scp_to_main(tmp.name, remote_path)
    os.unlink(tmp.name)

    if not scp_result["success"]:
        return scp_result

    result = ssh_exec(f"chmod +x {remote_path} && bash {remote_path} && rm {remote_path}", timeout=180)
    log_upgrade("custom_script", description, result["success"], result["stdout"][:300])

    if result["success"]:
        send_telegram(f"Script executed:\n<code>{result['stdout'][:500]}</code>")
    else:
        send_telegram(f"Script FAILED:\n<code>{result['stderr'][:500]}</code>")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════════════════════════════════════════

UPGRADE_TYPES = {
    "deploy_code": deploy_code,
    "deploy_skill": deploy_skill,
    "update_config": update_config,
    "restart_service": restart_service,
    "install_package": install_package,
    "run_migration": run_migration,
    "custom_script": custom_script,
}

if __name__ == "__main__":
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    upgrade_type = args.pop("type", None)

    if not upgrade_type:
        print("Available upgrade types:")
        for t in UPGRADE_TYPES:
            print(f"  - {t}")
        sys.exit(0)

    handler = UPGRADE_TYPES.get(upgrade_type)
    if not handler:
        print(f"Unknown upgrade type: {upgrade_type}")
        print(f"Available: {list(UPGRADE_TYPES.keys())}")
        sys.exit(1)

    result = handler(**args)
    print(json.dumps(result, indent=2))
