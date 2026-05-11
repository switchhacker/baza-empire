#!/usr/bin/env bash
# Empire State — RECENT auto-sync
#
# Reads the tail of baza-session-log.md, distills each `### YYYY-MM-DD HH:MM | <topic>`
# heading from the last 24h into a one-line bullet, prepends new bullets into the
# `## RECENT` section of EMPIRE_STATE.md, dedupes, and trims to 15 most-recent lines.
#
# Idempotent. Safe to run repeatedly. No-op if nothing new.
#
# Usage: bash scripts/empire_state_recent_sync.sh

set -euo pipefail

FRAMEWORK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_FILE="$FRAMEWORK_DIR/EMPIRE_STATE.md"
SESSION_LOG="$HOME/Desktop/baza-session-log.md"

[[ -f "$STATE_FILE" ]] || { echo "EMPIRE_STATE.md missing at $STATE_FILE — aborting"; exit 1; }
[[ -f "$SESSION_LOG" ]] || { echo "no session-log at $SESSION_LOG — no-op"; exit 0; }

# Pull all session-log headings from the last 24h, distill to one-line bullets.
# Heading format: ### YYYY-MM-DD HH:MM | <topic>
TODAY=$(date '+%Y-%m-%d')
YESTERDAY=$(date -d "yesterday" '+%Y-%m-%d' 2>/dev/null || date -v-1d '+%Y-%m-%d')

NEW_BULLETS=$(
    awk -v today="$TODAY" -v yday="$YESTERDAY" '
        /^### [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} \|/ {
            date = $2
            if (date == today || date == yday) {
                # Strip "### YYYY-MM-DD HH:MM | " prefix → keep just the topic
                sub(/^### [0-9-]+ [0-9:]+ \| /, "")
                # Skip generic markers
                if ($0 ~ /^Session (started|end)/) next
                # Truncate to ~100 chars per bullet
                line = $0
                if (length(line) > 100) line = substr(line, 1, 100) "…"
                print "- " date " " line
            }
        }
    ' "$SESSION_LOG"
)

if [[ -z "$NEW_BULLETS" ]]; then
    echo "no new session-log entries today/yesterday — no-op"
    exit 0
fi

# Build the new RECENT section:
#   new bullets (top), deduped against existing, capped to 15 lines.
TMP=$(mktemp)
trap "rm -f $TMP" EXIT

python3 - "$STATE_FILE" "$TMP" <<PY
import re, sys, os
state_path, tmp_path = sys.argv[1], sys.argv[2]
new_bullets = """$NEW_BULLETS""".strip().splitlines()

with open(state_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Extract existing RECENT section
m = re.search(r'(^## RECENT\s*$)(.*?)(?=^## |\Z)', content, re.MULTILINE | re.DOTALL)
existing_bullets = []
if m:
    body = m.group(2).strip()
    for line in body.splitlines():
        line = line.strip()
        if line.startswith('-'):
            existing_bullets.append(line)

# Prepend new bullets, dedupe by exact line, cap at 15 most recent
seen = set()
combined = []
for b in new_bullets + existing_bullets:
    if b in seen:
        continue
    seen.add(b)
    combined.append(b)
combined = combined[:15]

new_section = "## RECENT\n" + "\n".join(combined)

# Replace
if m:
    new_content = content[:m.start()] + new_section + "\n\n" + content[m.end():].lstrip()
else:
    # No RECENT section yet — append before first ## TOPIC or at end
    insert_at = content.find("\n## TOPIC:")
    if insert_at < 0:
        new_content = content.rstrip() + "\n\n" + new_section + "\n"
    else:
        new_content = content[:insert_at].rstrip() + "\n\n" + new_section + "\n\n" + content[insert_at:].lstrip()

with open(tmp_path, 'w', encoding='utf-8') as f:
    f.write(new_content)
PY

# Compare — only overwrite if changed
if ! cmp -s "$STATE_FILE" "$TMP"; then
    cp "$TMP" "$STATE_FILE"
    echo "EMPIRE_STATE.md RECENT updated"
else
    echo "no net change"
fi
