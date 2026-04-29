#!/usr/bin/env bash
# bootable_clone.sh — Restore a saved bootable image to a target USB.
#
# Usage:
#   bootable_clone.sh                       # list saved images
#   bootable_clone.sh <image-dir> <device>  # clone <image>/disk.img.zst to <device>
#
# Examples:
#   bootable_clone.sh
#   bootable_clone.sh /mnt/empirepool/cloud/1/BootableImages/2026-04-28-MSI-BOOT /dev/sdg
#
# Behavior:
#   - Refuses to write to internal/system disks.
#   - Verifies target device size >= image's raw size.
#   - Requires explicit y/N confirmation showing source/target/sizes/sha.
#   - Streams: zstd -d → tee(sha256) → dd to target. Verifies sha matches the
#     captured raw hash. Then partprobes the device.
set -euo pipefail

CLOUD_ROOT="/mnt/empirepool/cloud/1"
DEST_BASE="$CLOUD_ROOT/BootableImages"

list_images() {
  if [[ ! -d "$DEST_BASE" ]]; then
    echo "no images at $DEST_BASE" >&2
    return
  fi
  printf "%-40s  %-12s  %-12s  %s\n" "IMAGE DIR" "RAW SIZE" "COMPRESSED" "BOOTABLE"
  printf -- "------------------------------------------------------------------------------------------------\n"
  for d in "$DEST_BASE"/*/; do
    [[ -d "$d" ]] || continue
    meta="$d/metadata.json"
    if [[ -f "$meta" ]]; then
      raw="$(python3 -c "import json; d=json.load(open('$meta')); print(d.get('size_human','?'))")"
      cmp="$(python3 -c "import json; d=json.load(open('$meta')); print(d.get('compressed_human','?'))")"
      boot="$(python3 -c "import json; d=json.load(open('$meta')); print(d.get('appears_bootable',False))")"
      printf "%-40s  %-12s  %-12s  %s\n" "$(basename "$d")" "$raw" "$cmp" "$boot"
    else
      printf "%-40s  %-12s  %-12s  %s\n" "$(basename "$d")" "?" "?" "?"
    fi
  done
}

if [[ $# -eq 0 ]]; then
  list_images
  exit 0
fi

IMG_DIR="${1:-}"
TARGET="${2:-}"
if [[ -z "$IMG_DIR" || -z "$TARGET" ]]; then
  echo "usage: $0 [image-dir] [target-device]" >&2
  echo "       $0   (no args lists saved images)" >&2
  exit 2
fi

# Allow image dir to be a basename (resolve under DEST_BASE)
if [[ ! -d "$IMG_DIR" && -d "$DEST_BASE/$IMG_DIR" ]]; then
  IMG_DIR="$DEST_BASE/$IMG_DIR"
fi
[[ -d "$IMG_DIR" ]] || { echo "error: image dir not found: $IMG_DIR" >&2; exit 2; }
IMG="$IMG_DIR/disk.img.zst"
META="$IMG_DIR/metadata.json"
HASH="$IMG_DIR/sha256.txt"
[[ -f "$IMG" ]]  || { echo "error: $IMG missing" >&2; exit 2; }
[[ -f "$META" ]] || { echo "error: $META missing" >&2; exit 2; }
[[ -b "$TARGET" ]] || { echo "error: $TARGET not a block device" >&2; exit 2; }

# Safety: refuse internal / pool devices
TGT_NAME="$(basename "$TARGET")"
case "$TGT_NAME" in
  sda*|sdb*|sdc*|sdd*|sde*|nvme0*|nvme1*)
    echo "refused: $TARGET looks like an internal disk" >&2
    exit 3
    ;;
esac
HOTPLUG="$(lsblk -dn -o HOTPLUG "$TARGET" 2>/dev/null | tr -d '[:space:]' || echo 0)"
RM_FLAG="$(lsblk -dn -o RM "$TARGET" 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [[ "$HOTPLUG" != "1" && "$RM_FLAG" != "1" ]]; then
  echo "refused: $TARGET is not removable/hot-plug" >&2
  exit 3
fi

# Size check
RAW_BYTES="$(python3 -c "import json; d=json.load(open('$META')); print(d['size_bytes'])")"
TGT_BYTES="$(sudo blockdev --getsize64 "$TARGET")"
if (( TGT_BYTES < RAW_BYTES )); then
  echo "refused: target ($TGT_BYTES B) smaller than image raw size ($RAW_BYTES B)" >&2
  exit 4
fi

EXPECT_SHA="$(awk '{print $1; exit}' "$HASH" 2>/dev/null || echo "")"
SRC_LABEL="$(python3 -c "import json; d=json.load(open('$META')); print(d.get('label',''))")"
SRC_RAW_HUMAN="$(python3 -c "import json; d=json.load(open('$META')); print(d.get('size_human','?'))")"
TGT_HUMAN="$(numfmt --to=iec --suffix=B "$TGT_BYTES" 2>/dev/null || echo "$TGT_BYTES")"

cat <<EOF

About to clone:
  source dir:    $IMG_DIR
  source label:  $SRC_LABEL
  source size:   $SRC_RAW_HUMAN  (raw, will be decompressed from zstd)
  target dev:    $TARGET
  target size:   $TGT_HUMAN
  expected sha:  $EXPECT_SHA

THIS WILL DESTROY EVERYTHING ON $TARGET.
EOF
read -rp "Type the device name '$TARGET' to confirm: " ACK
if [[ "$ACK" != "$TARGET" ]]; then
  echo "aborted." >&2
  exit 5
fi

# Unmount any partitions on target
for p in ${TARGET}*; do
  [[ "$p" == "$TARGET" ]] && continue
  if mount | grep -q "^$p "; then
    echo "unmounting $p"
    sudo umount "$p" 2>/dev/null || true
  fi
done

echo "[$(date -Is)] cloning $IMG → $TARGET"
TMP_SHA="$(mktemp)"
zstd -d -q -c "$IMG" \
  | tee >(sha256sum | awk '{print $1}' > "$TMP_SHA") \
  | sudo dd of="$TARGET" bs=4M status=progress conv=fsync

GOT_SHA="$(cat "$TMP_SHA")"
rm -f "$TMP_SHA"

if [[ -n "$EXPECT_SHA" && "$GOT_SHA" != "$EXPECT_SHA" ]]; then
  echo "ERROR: sha mismatch on decompression!" >&2
  echo "  expected: $EXPECT_SHA" >&2
  echo "  actual:   $GOT_SHA"   >&2
  exit 6
fi
echo "✓ wrote raw image to $TARGET; sha matches ($GOT_SHA)"

sudo partprobe "$TARGET" 2>/dev/null || true
sudo udevadm settle 2>/dev/null || true
echo "✓ partprobe done — $TARGET should now mirror the source drive."
