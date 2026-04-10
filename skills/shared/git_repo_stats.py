#!/usr/bin/env python3
"""Skill: git_repo_stats — Git repository statistics.
Usage: ##SKILL:git_repo_stats{"path":"."}##"""
import os, json, subprocess
args = json.loads(os.environ.get("SKILL_ARGS","{}"))
path = args.get("path",".")
def run(cmd):
    try: return subprocess.check_output(cmd, shell=True, cwd=path, stderr=subprocess.DEVNULL, timeout=10).decode().strip()
    except: return "?"
commits = run("git rev-list --count HEAD")
branch = run("git branch --show-current")
contributors = run("git log --format='%aN' | sort -u | wc -l")
first = run("git log --reverse --format='%ai' | head -1")
last = run("git log -1 --format='%ai'")
files = run("find . -not -path './.git/*' -type f | wc -l")
size = run("du -sh .git | cut -f1")
print(f"Git Repository Stats")
print(f"  Branch: {branch}")
print(f"  Commits: {commits}")
print(f"  Contributors: {contributors}")
print(f"  Files: {files}")
print(f"  .git size: {size}")
print(f"  First commit: {first[:10] if first != '?' else '?'}")
print(f"  Last commit: {last[:10] if last != '?' else '?'}")
