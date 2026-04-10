#!/usr/bin/env python3
"""
Specter Voss — Log Analysis
Reads journalctl logs for baza-* services, surfaces errors, warnings, and tracebacks.

Args:
    {"service": "baza-dashboard", "lines": 50}
"""
import os, json, subprocess, re
from datetime import datetime
from collections import Counter

SKILL_ARGS = json.loads(os.environ.get("SKILL_ARGS", "{}"))


def run_cmd(cmd, timeout=15):
    """Run a shell command and return stdout."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=isinstance(cmd, str))
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out", 1
    except Exception as e:
        return f"ERROR: {e}", 1


def get_baza_services():
    """Discover all baza-* systemd units."""
    out, rc = run_cmd("systemctl list-units 'baza-*' --no-pager --no-legend --type=service --type=timer")
    services = []
    if rc == 0 and out:
        for line in out.split("\n"):
            parts = line.split()
            if parts:
                svc = parts[0].strip()
                if svc:
                    services.append(svc)
    if not services:
        # Fallback to known services
        services = [
            "baza-agents.service", "baza-dashboard.service",
            "baza-task-runner.service", "baza-task-runner.timer",
            "baza-tool-server.service",
        ]
    return services


def fetch_logs(service, lines=100):
    """Fetch recent journalctl logs for a service."""
    out, rc = run_cmd(f"journalctl -u {service} --no-pager -n {lines} --output=short-iso 2>/dev/null")
    if rc != 0 or not out:
        # Try without .service suffix or with it
        alt = service if ".service" in service else f"{service}.service"
        out, rc = run_cmd(f"journalctl -u {alt} --no-pager -n {lines} --output=short-iso 2>/dev/null")
    return out if out else ""


def analyze_logs(log_text):
    """Analyze log text for errors, warnings, and tracebacks."""
    lines = log_text.split("\n") if log_text else []

    errors = []
    warnings = []
    tracebacks = []
    in_traceback = False
    tb_buffer = []

    error_patterns = re.compile(r'\b(ERROR|CRITICAL|FATAL|Exception|raise |Traceback)\b', re.IGNORECASE)
    warn_patterns = re.compile(r'\b(WARNING|WARN|DeprecationWarning)\b', re.IGNORECASE)
    tb_start = re.compile(r'Traceback \(most recent call last\)', re.IGNORECASE)
    tb_end = re.compile(r'^(\w+Error|Exception|KeyError|ValueError|TypeError|RuntimeError|OSError|ConnectionError)', re.IGNORECASE)

    for line in lines:
        stripped = line.strip()

        # Traceback detection
        if tb_start.search(stripped):
            in_traceback = True
            tb_buffer = [stripped]
            continue
        if in_traceback:
            tb_buffer.append(stripped)
            if tb_end.match(stripped) or len(tb_buffer) > 30:
                tracebacks.append("\n".join(tb_buffer))
                in_traceback = False
                tb_buffer = []
            continue

        # Error/warning classification
        if error_patterns.search(stripped):
            errors.append(stripped[:200])
        elif warn_patterns.search(stripped):
            warnings.append(stripped[:200])

    return {
        "total_lines": len(lines),
        "errors": errors,
        "warnings": warnings,
        "tracebacks": tracebacks,
    }


def main():
    target_service = SKILL_ARGS.get("service", "")
    lines = int(SKILL_ARGS.get("lines", 100))

    print("=== BAZA LOG ANALYSIS ===")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    if target_service:
        # Single service scan
        services = [target_service if "." in target_service else f"{target_service}.service"]
    else:
        services = get_baza_services()

    if not services:
        print("No baza-* services found to scan.")
        return

    total_errors = 0
    total_warnings = 0
    total_tracebacks = 0

    for svc in services:
        log_text = fetch_logs(svc, lines)
        if not log_text:
            print(f"[{svc}] No logs available")
            print()
            continue

        analysis = analyze_logs(log_text)
        error_count = len(analysis["errors"])
        warn_count = len(analysis["warnings"])
        tb_count = len(analysis["tracebacks"])

        total_errors += error_count
        total_warnings += warn_count
        total_tracebacks += tb_count

        # Status indicator
        if tb_count > 0 or error_count > 5:
            indicator = "CRITICAL"
        elif error_count > 0:
            indicator = "ISSUES"
        elif warn_count > 0:
            indicator = "WARNINGS"
        else:
            indicator = "CLEAN"

        print(f"[{svc}] {indicator} — {analysis['total_lines']} lines | {error_count} errors | {warn_count} warnings | {tb_count} tracebacks")

        # Show tracebacks first (most important)
        if analysis["tracebacks"]:
            print(f"  TRACEBACKS:")
            for i, tb in enumerate(analysis["tracebacks"][:3]):
                print(f"  --- Traceback #{i+1} ---")
                for tbline in tb.split("\n")[:10]:
                    print(f"    {tbline}")
                if len(tb.split("\n")) > 10:
                    print(f"    ... ({len(tb.split(chr(10)))} lines total)")
            if len(analysis["tracebacks"]) > 3:
                print(f"  ... +{len(analysis['tracebacks']) - 3} more tracebacks")

        # Show unique errors
        if analysis["errors"]:
            unique_errors = list(dict.fromkeys(analysis["errors"]))  # dedupe preserving order
            print(f"  ERRORS ({len(unique_errors)} unique):")
            for err in unique_errors[:5]:
                print(f"    {err}")
            if len(unique_errors) > 5:
                print(f"    ... +{len(unique_errors) - 5} more")

        # Show unique warnings (abbreviated)
        if analysis["warnings"]:
            unique_warnings = list(dict.fromkeys(analysis["warnings"]))
            print(f"  WARNINGS ({len(unique_warnings)} unique):")
            for w in unique_warnings[:3]:
                print(f"    {w}")
            if len(unique_warnings) > 3:
                print(f"    ... +{len(unique_warnings) - 3} more")

        print()

    # Summary
    print("=" * 50)
    print(f"SUMMARY: {len(services)} services scanned")
    print(f"  Total errors: {total_errors}")
    print(f"  Total warnings: {total_warnings}")
    print(f"  Total tracebacks: {total_tracebacks}")

    if total_tracebacks > 0:
        print("  STATUS: CRITICAL — tracebacks detected, investigate immediately")
    elif total_errors > 10:
        print("  STATUS: DEGRADED — high error count")
    elif total_errors > 0:
        print("  STATUS: MINOR ISSUES — some errors present")
    else:
        print("  STATUS: HEALTHY — no significant issues")

    print("\n=== SCAN COMPLETE ===")


if __name__ == "__main__":
    main()
