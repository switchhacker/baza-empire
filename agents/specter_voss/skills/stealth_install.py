#!/usr/bin/env python3
"""
Skill: stealth_install
Specter Voss installs a package on the main server.
Requires Serge's approval via Telegram.

Usage: ##SKILL:stealth_install{"package":"requests","manager":"pip"}##
       ##SKILL:stealth_install{"package":"htop","manager":"apt"}##
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "openclaw", "tools"))
from stealth_upgrade import install_package

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
package = args.get("package", "")
manager = args.get("manager", "pip")

if not package:
    print("Error: 'package' name required")
    exit(1)

result = install_package(package=package, manager=manager)
if result.get("success"):
    print(f"Installed '{package}' via {manager}")
else:
    reason = result.get("reason", result.get("stderr", "unknown error"))
    print(f"Install failed: {reason}")
