#!/usr/bin/env bash
# Rebuild the skill manifest. Safe to run repeatedly; cheap (ast scan).
# Pulls the live tool-server registry too if it is reachable.
set -euo pipefail
cd "$(dirname "$0")/.."
venv/bin/python -m core.skill_registry --build
