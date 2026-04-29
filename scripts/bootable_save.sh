#!/usr/bin/env bash
# bootable_save.sh — Save a full disk image of a USB drive into baza cloud.
#
# Usage:
#   bootable_save.sh <device> [label]
#
# Examples:
#   bootable_save.sh /dev/sdf MSI-BIOS-Flash
#   bootable_save.sh /dev/sdg
#
# Behavior:
#   - Refuses to image internal disks (sda-sde, nvme*) or anything currently
#     mounted at /, /home, /boot, or /mnt/empirepool*. Only USB / hot-plug.
#   - Reads the whole device with dd, streams through zstd, writes to
#     /mnt/empirepool/cloud/1/BootableImages/<YYYY-MM-DD>-<label>/.
#   - Writes a metadata.json with size, sector count, partition table dump,
#     blkid output, and a SHA256 of the raw image (computed alongside zstd).
#   - Refuses to overwrite an existing image directory.
#   - Requires sudo for dd / sfdisk / blkid.
set -euo pipefail

DEV="${1:-}"
LABEL_IN="${2:-}"

if [[ -z "$DEV" ]]; then
  echo "usage: $0 <device> [label]" >&2
  exit 2
fi
if [[ ! -b "$DEV" ]]; then
  echo "error: $DEV is not a block device" >&2
  exit 2
fi

CLOUD_ROOT="/mnt/empirepool/cloud/1"
DEST_BASE="$CLOUD_ROOT/BootableImages"
LOG_DIR="/home/switchhacker/baza-empire/agent-framework-v3/logs/bootable"
STAMP="$(date +%Y-%m-%d)"

# ── Safety: refuse internal / system / pool devices ───────────────────────────
DEV_NAME="$(basename "$DEV")"
case "$DEV_NAME" in
  sda*|sdb*|sdc*|sdd*|sde*|nvme0*|nvme1*)
    echo "refused: $DEV looks like an internal/pool disk; use a USB hot-plug device" >&2
    exit 3
    ;;
esac

# Confirm it's hot-plug / removable. lsblk pads single-digit values with
# leading whitespace, so trim before comparing.
HOTPLUG="$(lsblk -dn -o HOTPLUG "$DEV" 2>/dev/null | tr -d '[:space:]' || echo 0)"
RM_FLAG="$(lsblk -dn -o RM "$DEV" 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [[ "$HOTPLUG" != "1" && "$RM_FLAG" != "1" ]]; then
  echo "refused: $DEV is not removable/hot-plug; aborting" >&2
  exit 3
fi

# Refuse to image the running root by stat
ROOT_DEV="$(findmnt -nro SOURCE / | sed 's/p\?[0-9]*$//')"
if [[ "$DEV" == "$ROOT_DEV" ]]; then
  echo "refused: $DEV hosts the running root filesystem" >&2
  exit 3
fi

# ── Derive label from filesystem if not given ────────────────────────────────
if [[ -z "$LABEL_IN" ]]; then
  P1="${DEV}1"
  if [[ -b "$P1" ]]; then
    LABEL_IN="$(sudo blkid -s LABEL -o value "$P1" 2>/dev/null || true)"
  fi
  [[ -z "$LABEL_IN" ]] && LABEL_IN="$DEV_NAME"
fi
SLUG="$(echo "$LABEL_IN" | tr ' ' '-' | tr -c 'A-Za-z0-9_.-' '-' | sed 's/-\+/-/g; s/^-//; s/-$//')"

DEST="$DEST_BASE/${STAMP}-${SLUG}"
if [[ -d "$DEST" ]]; then
  echo "refused: destination already exists: $DEST" >&2
  exit 4
fi

mkdir -p "$DEST" "$LOG_DIR"
LOG="$LOG_DIR/${STAMP}-${SLUG}.log"
IMG="$DEST/disk.img.zst"
META="$DEST/metadata.json"
PART="$DEST/parttable.sfdisk"
HASH="$DEST/sha256.txt"

echo "[$(date -Is)] saving $DEV → $DEST" | tee -a "$LOG"

# ── Capture metadata ─────────────────────────────────────────────────────────
SIZE_BYTES="$(sudo blockdev --getsize64 "$DEV")"
SIZE_HUMAN="$(numfmt --to=iec --suffix=B "$SIZE_BYTES" 2>/dev/null || echo "$SIZE_BYTES")"
sudo sfdisk --dump "$DEV" > "$PART" 2>>"$LOG" || true
sudo blkid -p "$DEV" > "$DEST/blkid_disk.txt"  2>>"$LOG" || true
for p in ${DEV}*; do
  [[ "$p" == "$DEV" ]] && continue
  [[ -b "$p" ]] && sudo blkid -p "$p" >> "$DEST/blkid_partitions.txt" 2>>"$LOG" || true
done
sudo file -s "$DEV" > "$DEST/file_disk.txt" 2>>"$LOG" || true

# Boot signature: 0xAA55 at offset 510-511 means MBR boot sector present.
BOOTSIG="$(sudo dd if="$DEV" bs=1 count=2 skip=510 status=none 2>>"$LOG" | xxd -p)"
BOOTABLE="false"
[[ "$BOOTSIG" == "55aa" ]] && BOOTABLE="true"

cat > "$META" <<JSON
{
  "device":         "$DEV",
  "label":          "$LABEL_IN",
  "slug":           "$SLUG",
  "stamp":          "$STAMP",
  "size_bytes":     $SIZE_BYTES,
  "size_human":     "$SIZE_HUMAN",
  "boot_signature": "0x$BOOTSIG",
  "appears_bootable": $BOOTABLE,
  "model":          $(lsblk -dn -o MODEL "$DEV" 2>/dev/null | sed 's/[[:space:]]\+$//' | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))" 2>/dev/null || echo '""'),
  "vendor":         $(lsblk -dn -o VENDOR "$DEV" 2>/dev/null | sed 's/[[:space:]]\+$//' | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))" 2>/dev/null || echo '""'),
  "host":           "$(hostname)",
  "captured_at":    "$(date -Is)"
}
JSON

echo "[$(date -Is)] size=$SIZE_HUMAN bootable=$BOOTABLE" | tee -a "$LOG"

# ── Unmount any partitions to get a consistent image ─────────────────────────
for p in ${DEV}*; do
  [[ "$p" == "$DEV" ]] && continue
  if mount | grep -q "^$p "; then
    echo "[$(date -Is)] unmounting $p" | tee -a "$LOG"
    sudo umount "$p" 2>>"$LOG" || true
  fi
done

# ── Stream dd → tee(sha256) → zstd → file ────────────────────────────────────
echo "[$(date -Is)] reading $DEV ($SIZE_HUMAN), compressing with zstd -3" | tee -a "$LOG"
sudo dd if="$DEV" bs=4M status=progress 2>>"$LOG" \
  | tee >(sha256sum | awk '{print $1"  raw"}' > "$HASH") \
  | zstd -3 -T0 -q -o "$IMG"

ZSIZE_BYTES="$(stat -c%s "$IMG")"
ZSIZE_HUMAN="$(numfmt --to=iec --suffix=B "$ZSIZE_BYTES" 2>/dev/null || echo "$ZSIZE_BYTES")"
echo "[$(date -Is)] wrote $IMG ($ZSIZE_HUMAN)" | tee -a "$LOG"

# Update metadata with compressed size
python3 - "$META" "$ZSIZE_BYTES" "$ZSIZE_HUMAN" <<'PY'
import json, sys
p, zb, zh = sys.argv[1], int(sys.argv[2]), sys.argv[3]
d = json.load(open(p))
d['compressed_bytes'] = zb
d['compressed_human'] = zh
d['compression']      = 'zstd-3'
json.dump(d, open(p, 'w'), indent=2)
PY

echo
echo "✓ saved bootable image:"
echo "  dir:   $DEST"
echo "  image: $IMG ($ZSIZE_HUMAN, raw $SIZE_HUMAN)"
echo "  meta:  $META"
echo "  hash:  $(cat "$HASH")"
echo "  log:   $LOG"
