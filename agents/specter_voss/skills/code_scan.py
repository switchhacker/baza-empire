#!/usr/bin/env python3
"""
Specter Voss — Codebase Health Scan
Git status, commit history, file type counts, large files, TODO/FIXME/HACK scan.

Args:
    {"path": "specific/path"}  — scan a subtree instead of full framework
"""
import os, json, subprocess
from collections import Counter
from datetime import datetime

SKILL_ARGS = json.loads(os.environ.get("SKILL_ARGS", "{}"))
FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def run_cmd(cmd, cwd=None, timeout=15):
    """Run a shell command and return stdout."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=isinstance(cmd, str), cwd=cwd or FRAMEWORK_DIR,
        )
        return r.stdout.strip() if r.returncode == 0 else f"ERROR: {r.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out"
    except Exception as e:
        return f"ERROR: {e}"


def git_status():
    """Run git status summary."""
    status = run_cmd("git status --short")
    if status.startswith("ERROR"):
        return status

    lines = status.split("\n") if status else []
    modified = sum(1 for l in lines if l.strip().startswith("M"))
    added = sum(1 for l in lines if l.strip().startswith("A"))
    deleted = sum(1 for l in lines if l.strip().startswith("D"))
    untracked = sum(1 for l in lines if l.strip().startswith("?"))
    other = len(lines) - modified - added - deleted - untracked

    summary = f"  Modified: {modified} | Added: {added} | Deleted: {deleted} | Untracked: {untracked}"
    if other > 0:
        summary += f" | Other: {other}"
    summary += f" | Total changes: {len(lines)}"
    return summary


def git_log(count=10):
    """Show recent commits."""
    return run_cmd(f"git log --oneline --no-decorate -n {count}")


def count_files_by_type(scan_path):
    """Count files by extension."""
    ext_counts = Counter()
    total_files = 0
    for root, dirs, files in os.walk(scan_path):
        # Skip common non-source directories
        dirs[:] = [d for d in dirs if d not in {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            ".tox", ".mypy_cache", ".pytest_cache",
        }]
        for f in files:
            total_files += 1
            ext = os.path.splitext(f)[1].lower() or "(no ext)"
            ext_counts[ext] += 1
    return total_files, ext_counts


def find_large_files(scan_path, threshold_kb=500):
    """Find files larger than threshold."""
    large = []
    for root, dirs, files in os.walk(scan_path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "venv", ".venv"}]
        for f in files:
            path = os.path.join(root, f)
            try:
                size = os.path.getsize(path)
                if size > threshold_kb * 1024:
                    rel = os.path.relpath(path, FRAMEWORK_DIR)
                    large.append((rel, size))
            except OSError:
                pass
    return sorted(large, key=lambda x: -x[1])[:20]


def pycache_bloat(scan_path):
    """Count __pycache__ directories and total .pyc size."""
    cache_dirs = 0
    total_bytes = 0
    for root, dirs, files in os.walk(scan_path):
        if "__pycache__" in dirs:
            cache_dirs += 1
        if os.path.basename(root) == "__pycache__":
            for f in files:
                try:
                    total_bytes += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return cache_dirs, total_bytes


def scan_markers(scan_path):
    """Scan for TODO, FIXME, HACK comments in Python files."""
    markers = {"TODO": [], "FIXME": [], "HACK": []}
    for root, dirs, files in os.walk(scan_path):
        dirs[:] = [d for d in dirs if d not in {
            ".git", "node_modules", "__pycache__", "venv", ".venv",
        }]
        for f in files:
            if not f.endswith((".py", ".js", ".html", ".yaml", ".yml", ".sh")):
                continue
            path = os.path.join(root, f)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        for marker in markers:
                            if marker in line:
                                rel = os.path.relpath(path, FRAMEWORK_DIR)
                                snippet = line.strip()[:100]
                                markers[marker].append(f"{rel}:{lineno}: {snippet}")
            except Exception:
                pass
    return markers


def main():
    scan_path = SKILL_ARGS.get("path")
    if scan_path:
        scan_path = os.path.join(FRAMEWORK_DIR, scan_path)
        if not os.path.exists(scan_path):
            print(f"ERROR: path does not exist: {scan_path}")
            return
    else:
        scan_path = FRAMEWORK_DIR

    print("=== CODEBASE HEALTH SCAN ===")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Scan path: {os.path.relpath(scan_path, FRAMEWORK_DIR) if scan_path != FRAMEWORK_DIR else '(full framework)'}")
    print()

    # Git status
    print("[GIT STATUS]")
    print(git_status())
    branch = run_cmd("git branch --show-current")
    print(f"  Branch: {branch}")
    print()

    # Git log
    print("[RECENT COMMITS]")
    log = git_log()
    if log:
        for line in log.split("\n"):
            print(f"  {line}")
    print()

    # File counts
    print("[FILE TYPES]")
    total, ext_counts = count_files_by_type(scan_path)
    print(f"  Total files: {total}")
    for ext, count in ext_counts.most_common(15):
        print(f"  {ext:<12} {count:>5}")
    if len(ext_counts) > 15:
        print(f"  ... +{len(ext_counts) - 15} more types")
    print()

    # Large files
    print("[LARGE FILES] (> 500 KB)")
    large = find_large_files(scan_path)
    if large:
        for path, size in large:
            size_str = f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / (1024*1024):.1f} MB"
            print(f"  {size_str:<10} {path}")
    else:
        print("  None found")
    print()

    # Pycache
    print("[__PYCACHE__ BLOAT]")
    cache_dirs, cache_bytes = pycache_bloat(scan_path)
    print(f"  Directories: {cache_dirs} | Total size: {cache_bytes / 1024:.0f} KB")
    print()

    # Markers
    print("[CODE MARKERS]")
    markers = scan_markers(scan_path)
    for marker, hits in markers.items():
        print(f"  {marker}: {len(hits)} occurrences")
        for hit in hits[:5]:
            print(f"    {hit}")
        if len(hits) > 5:
            print(f"    ... +{len(hits) - 5} more")
    print()

    print("=== SCAN COMPLETE ===")


if __name__ == "__main__":
    main()
