#!/usr/bin/env bash
# Baza Empire restore helper. Reverses scripts/backup.sh.
# Usage: ./restore.sh <backup-stamp> [--yes]
# Example: ./restore.sh 2026-04-22T03-00-00 --yes
set -euo pipefail

FRAMEWORK=/home/switchhacker/baza-empire/agent-framework-v3
DEST=/mnt/empirepool/backups/baza-empire
STAMP="${1:-}"
CONFIRM="${2:-}"

if [ -z "$STAMP" ]; then
  echo "Available backups:"
  echo "--- daily ---"
  ls -1 "$DEST/daily" 2>/dev/null | tail -20
  echo "--- weekly ---"
  ls -1 "$DEST/weekly" 2>/dev/null | tail -20
  echo
  echo "Usage: $0 <stamp> [--yes]"
  exit 1
fi

SRC="$DEST/daily/$STAMP"
[ -d "$SRC" ] || SRC="$DEST/weekly/$STAMP"
[ -d "$SRC" ] || { echo "ERROR: snapshot $STAMP not found"; exit 1; }

echo "About to restore from: $SRC"
echo "This will OVERWRITE current state. Services should be stopped first."
if [ "$CONFIRM" != "--yes" ]; then
  read -rp "Type YES to proceed: " answer
  [ "$answer" = "YES" ] || { echo "aborted"; exit 1; }
fi

echo "Stopping services..."
sudo systemctl stop 'baza-agent-*.service' baza-dashboard.service baza-task-runner.service || true

# SQLite
if [ -f "$SRC/baza_projects.db.gz" ]; then
  cp "$FRAMEWORK/dashboard/baza_projects.db" "$FRAMEWORK/dashboard/baza_projects.db.pre-restore-$(date +%s)" 2>/dev/null || true
  gunzip -c "$SRC/baza_projects.db.gz" > "$FRAMEWORK/dashboard/baza_projects.db"
  echo "restored baza_projects.db"
fi

# Postgres
if [ -f "$SRC/baza_agents.dump" ]; then
  echo "restoring postgres baza_agents..."
  PGPASSWORD="${DB_PASSWORD:-baza2026}" dropdb -h localhost -U switchhacker baza_agents || true
  PGPASSWORD="${DB_PASSWORD:-baza2026}" createdb -h localhost -U switchhacker baza_agents
  PGPASSWORD="${DB_PASSWORD:-baza2026}" pg_restore -h localhost -U switchhacker -d baza_agents "$SRC/baza_agents.dump"
  echo "restored baza_agents"
fi

# Artifacts
if [ -f "$SRC/artifacts.tar.gz" ]; then
  tar -xzf "$SRC/artifacts.tar.gz" -C "$FRAMEWORK/dashboard"
  echo "restored artifacts"
fi

echo "Restore complete. Restart services:"
echo "  sudo systemctl start baza-dashboard.service 'baza-agent-*.service'"
