#!/usr/bin/env bash
# import_drive.sh — Migrate a mounted drive into Serge's baza cloud.
#
# Usage:
#   import_drive.sh <mount_path> <label> [--register]
#
# Examples:
#   import_drive.sh "/media/switchhacker/picture this" Mac-SSD-picture-this --register
#   import_drive.sh "/media/switchhacker/0123-4567"   iPhone-Card-0123-4567 --register
#
# Behavior:
#   - Copies files to /mnt/empirepool/cloud/1/Imports/YYYY-MM-DD-<label>/ (preserves source layout).
#   - Skips macOS/iOS junk (._*, .DS_Store, .Spotlight-V100, .fseventsd, .Trashes, .TemporaryItems, .DocumentRevisions-V100, System Volume Information).
#   - Uses rsync --checksum on a second pass to verify every byte transferred.
#   - Writes a SHA256 manifest next to the import.
#   - Logs everything to framework/logs/imports/<stamp>-<label>.log.
#   - Never deletes source files. Ask the user explicitly before freeing the source drive.
#   - With --register, inserts rows into baza_cloud_files and nudges the media indexer.
set -euo pipefail

MOUNT="${1:-}"
LABEL="${2:-}"
REGISTER="${3:-}"

if [[ -z "$MOUNT" || -z "$LABEL" ]]; then
  echo "usage: $0 <mount_path> <label> [--register]" >&2
  exit 2
fi
if [[ ! -d "$MOUNT" ]]; then
  echo "error: mount path not a directory: $MOUNT" >&2
  exit 2
fi

FRAMEWORK="/home/switchhacker/baza-empire/agent-framework-v3"
CLOUD_ROOT="/mnt/empirepool/cloud/1"
STAMP="$(date +%Y-%m-%d)"
SLUG="$(echo "$LABEL" | tr ' ' '-' | tr -c 'A-Za-z0-9_.-' '-' | sed 's/-\+/-/g; s/^-//; s/-$//')"
DEST="$CLOUD_ROOT/Imports/${STAMP}-${SLUG}"
LOG_DIR="$FRAMEWORK/logs/imports"
LOG="$LOG_DIR/${STAMP}-${SLUG}.log"
MANIFEST="$DEST/.import_manifest.sha256"
META="$DEST/.import_meta.txt"

mkdir -p "$DEST" "$LOG_DIR"

# Junk patterns we drop on import.
EXCLUDES=(
  --exclude='._*'                        # Mac AppleDouble sidecars
  --exclude='.DS_Store'
  --exclude='.Spotlight-V100'
  --exclude='.fseventsd'
  --exclude='.Trashes'
  --exclude='.TemporaryItems'
  --exclude='.DocumentRevisions-V100'
  --exclude='.HFS+ Private Directory Data'
  --exclude='.journal'
  --exclude='.journal_info_block'
  --exclude='System Volume Information'
  --exclude='$RECYCLE.BIN'
  --exclude='lost+found'
)

{
  echo "=== import_drive.sh start $(date -Is) ==="
  echo "source : $MOUNT"
  echo "dest   : $DEST"
  echo "label  : $LABEL"
  echo

  SRC_BYTES="$(du -sb --exclude='.Spotlight-V100' --exclude='.fseventsd' --exclude='.DocumentRevisions-V100' --exclude='.TemporaryItems' "$MOUNT" 2>/dev/null | awk '{print $1}')"
  echo "source bytes (approx): $SRC_BYTES"
  echo

  # Pass 1: fast copy. Tolerate per-file read failures (HFS+ Photos caches,
  # locked iOS sidecars, etc) — they're logged and retried in pass 2.
  echo "--- pass 1: rsync size+mtime ---"
  rsync -aH --info=progress2,stats2 --no-owner --no-group \
        --modify-window=2 --ignore-missing-args \
        "${EXCLUDES[@]}" \
        "$MOUNT/" "$DEST/" || echo "WARN pass1 exit=$? — continuing to verification"

  # Pass 2: checksum verification. Only retransfers files whose content differs.
  # Continue on errors — we report unreadable-source files at the end.
  echo
  echo "--- pass 2: rsync --checksum verification ---"
  set +e
  rsync -aHn --checksum --itemize-changes --no-owner --no-group \
        --modify-window=2 \
        "${EXCLUDES[@]}" \
        "$MOUNT/" "$DEST/" > /tmp/import_verify_$$.log 2>&1
  VERIFY_EXIT=$?
  DIFFS=$(grep -cE '^>f' /tmp/import_verify_$$.log || true)
  SRC_ERRS=$(grep -cE "failed verification|Permission denied|No such file|cannot open" /tmp/import_verify_$$.log || true)
  if [[ "$DIFFS" -gt 0 ]]; then
    echo "WARN: $DIFFS files differ — rerunning with --checksum to correct"
    rsync -aH --checksum --info=progress2 --no-owner --no-group \
          --modify-window=2 \
          "${EXCLUDES[@]}" \
          "$MOUNT/" "$DEST/" || echo "WARN: pass2 exit=$? (some unreadable-source files)"
  else
    echo "OK: no checksum differences (verify exit=$VERIFY_EXIT, diffs=$DIFFS)"
  fi
  if [[ "$SRC_ERRS" -gt 0 ]]; then
    echo "NOTE: $SRC_ERRS source-side read errors logged (HFS+ cache / permission-locked files)."
    echo "      See: $LOG for detail. These are rarely user data."
  fi
  rm -f /tmp/import_verify_$$.log
  set -e

  # Manifest.
  echo
  echo "--- sha256 manifest ---"
  (cd "$DEST" && find . -type f ! -name '.import_manifest.sha256' ! -name '.import_meta.txt' -print0 \
    | xargs -0 -P 4 -I{} sha256sum "{}" > "$MANIFEST")
  FILE_COUNT="$(wc -l < "$MANIFEST" | tr -d ' ')"
  DEST_BYTES="$(du -sb "$DEST" | awk '{print $1}')"
  echo "files: $FILE_COUNT"
  echo "bytes: $DEST_BYTES"

  cat > "$META" <<EOF
import_source   : $MOUNT
import_label    : $LABEL
import_dest     : $DEST
imported_at     : $(date -Is)
file_count      : $FILE_COUNT
dest_bytes      : $DEST_BYTES
source_bytes    : $SRC_BYTES
host            : $(hostname)
EOF

  # Optional DB registration so files show up in /cloud UI queries and the media index.
  if [[ "$REGISTER" == "--register" ]]; then
    echo
    echo "--- registering in baza_cloud_files ---"
    python3 "$FRAMEWORK/scripts/register_import.py" "$DEST" || echo "register_import.py failed (non-fatal)"
  fi

  echo
  echo "=== import_drive.sh done $(date -Is) ==="
} 2>&1 | tee -a "$LOG"
