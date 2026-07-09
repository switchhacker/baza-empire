#!/usr/bin/env bash
# Baza Empire unified backup: SQLite (baza_projects.db), Postgres (baza_agents),
# image captions DB, agent artifacts, and critical configs.
# Triggered by systemd timer baza-backup.timer (see baza-backup.service).
set -euo pipefail

FRAMEWORK=/home/switchhacker/baza-empire/agent-framework-v3
DEST=/mnt/empirepool/backups/baza-empire
RETENTION_DAILY=14
RETENTION_WEEKLY=8
LOG=/home/switchhacker/baza-empire/agent-framework-v3/logs/backup.log

mkdir -p "$DEST/daily" "$DEST/weekly" "$(dirname "$LOG")"

STAMP=$(date +%Y-%m-%dT%H-%M-%S)
DOW=$(date +%u)   # 1=Mon..7=Sun
TARGET="$DEST/daily/$STAMP"
mkdir -p "$TARGET"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

log "=== START backup $STAMP ==="

# 1. SQLite: baza_projects.db (AHBCO business data) via .backup for consistent snapshot
if [ -f "$FRAMEWORK/dashboard/baza_projects.db" ]; then
  sqlite3 "$FRAMEWORK/dashboard/baza_projects.db" ".backup '$TARGET/baza_projects.db'"
  gzip -f "$TARGET/baza_projects.db"
  log "sqlite baza_projects.db backed up ($(stat -c%s "$TARGET/baza_projects.db.gz") bytes)"
fi

# 2. SQLite: image captions (if present)
if [ -f "$FRAMEWORK/baza_context.db" ]; then
  sqlite3 "$FRAMEWORK/baza_context.db" ".backup '$TARGET/baza_context.db'"
  gzip -f "$TARGET/baza_context.db"
  log "sqlite baza_context.db backed up"
fi
for db in "$FRAMEWORK"/dashboard/*.db "$FRAMEWORK"/dashboard/*.sqlite; do
  [ -f "$db" ] || continue
  name=$(basename "$db")
  [ "$name" = "baza_projects.db" ] && continue
  sqlite3 "$db" ".backup '$TARGET/$name'" 2>/dev/null && gzip -f "$TARGET/$name" && log "sqlite $name backed up"
done

# 3. PostgreSQL: baza_agents (agent memory, knowledge, tasks)
if PGPASSWORD="${DB_PASSWORD:-baza2026}" pg_dump -h localhost -U switchhacker -d baza_agents -Fc -f "$TARGET/baza_agents.dump" 2>>"$LOG"; then
  log "postgres baza_agents dumped ($(stat -c%s "$TARGET/baza_agents.dump") bytes)"
else
  log "WARN: postgres dump failed"
fi

# 4. Critical configs (encrypted at rest on local pool — file perms only protection)
tar -czf "$TARGET/configs.tar.gz" \
  -C "$FRAMEWORK" config configs 2>/dev/null || log "WARN: configs tar failed"

# 5. Agent artifacts (project deliverables, infra reports, curated docs)
if [ -d "$FRAMEWORK/dashboard/artifacts" ]; then
  tar -czf "$TARGET/artifacts.tar.gz" -C "$FRAMEWORK/dashboard" artifacts 2>/dev/null \
    && log "artifacts tarball created ($(stat -c%s "$TARGET/artifacts.tar.gz") bytes)"
fi

# 5b. Editor upload assets (referenced by dashboard/ui_overrides.db "image" overrides)
if [ -d "$FRAMEWORK/dashboard/static/uploads" ]; then
  tar -czf "$TARGET/uploads.tar.gz" -C "$FRAMEWORK/dashboard/static" uploads 2>/dev/null \
    && log "uploads tarball created ($(stat -c%s "$TARGET/uploads.tar.gz") bytes)"
fi

# 6. Agent persona overrides + crons
tar -czf "$TARGET/agents.tar.gz" -C "$FRAMEWORK" \
  --exclude='**/__pycache__' \
  agents 2>/dev/null || log "WARN: agents tar failed"

# 7. Write manifest
{
  echo "timestamp=$STAMP"
  echo "host=$(hostname)"
  echo "git_head=$(cd "$FRAMEWORK" && git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "framework=$FRAMEWORK"
  echo "files:"
  (cd "$TARGET" && ls -la) | sed 's/^/  /'
} > "$TARGET/MANIFEST.txt"

# 8. Promote to weekly on Sundays
if [ "$DOW" = "7" ]; then
  cp -al "$TARGET" "$DEST/weekly/$STAMP" 2>/dev/null || cp -r "$TARGET" "$DEST/weekly/$STAMP"
  log "promoted to weekly snapshot"
fi

# 9. Rotate: keep last N daily, N weekly
find "$DEST/daily" -maxdepth 1 -mindepth 1 -type d | sort | head -n -"$RETENTION_DAILY" | xargs -r rm -rf
find "$DEST/weekly" -maxdepth 1 -mindepth 1 -type d | sort | head -n -"$RETENTION_WEEKLY" | xargs -r rm -rf

# 10. Summary
TOTAL_DAILY=$(find "$DEST/daily" -maxdepth 1 -mindepth 1 -type d | wc -l)
TOTAL_WEEKLY=$(find "$DEST/weekly" -maxdepth 1 -mindepth 1 -type d | wc -l)
SIZE=$(du -sh "$DEST" | awk '{print $1}')
log "=== DONE backup $STAMP | daily=$TOTAL_DAILY weekly=$TOTAL_WEEKLY total_size=$SIZE ==="
