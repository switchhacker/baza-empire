#!/usr/bin/env bash
# Baza Empire — Download the pinned NerdMinerV2 firmware for ESP32-S3 devkits.
# Retries on network failure; picks the S3-devkit variant specifically (native USB,
# no display). Writes to firmware/nerdminer/NerdMinerV2_S3_devkit.bin.
set -euo pipefail

FRAMEWORK=/home/switchhacker/baza-empire/agent-framework-v3
FIRMWARE_DIR="$FRAMEWORK/firmware/nerdminer"
mkdir -p "$FIRMWARE_DIR"

# Pinned release — if you want to bump, grab the tag from
# https://github.com/BitMaker-hub/NerdMiner_v2/releases and replace here.
# Leave empty to use 'latest' (less reproducible but fresher).
RELEASE_TAG="${NERDMINER_RELEASE_TAG:-latest}"

# Variant selector — tries these patterns in order against the release's assets.
# Generic ESP32-S3 devkits (no display, native USB) match the first pattern.
VARIANT_PATTERNS=(
    "devkit"
    "S3-DevKitC"
    "no-display"
    "nodisplay"
    "DevKit"
)

if [ "$RELEASE_TAG" = "latest" ]; then
    API_URL="https://api.github.com/repos/BitMaker-hub/NerdMiner_v2/releases/latest"
else
    API_URL="https://api.github.com/repos/BitMaker-hub/NerdMiner_v2/releases/tags/$RELEASE_TAG"
fi

echo "fetching release info from $API_URL ..."
RELEASE_JSON=""
for attempt in 1 2 3 4 5; do
    RELEASE_JSON=$(curl -sS -m 20 "$API_URL" 2>/dev/null || true)
    if [ -n "$RELEASE_JSON" ] && echo "$RELEASE_JSON" | grep -q '"tag_name"'; then
        break
    fi
    echo "attempt $attempt failed — retrying in 10s..."
    sleep 10
done

[ -n "$RELEASE_JSON" ] || { echo "ERROR: could not reach GitHub after 5 tries"; exit 1; }

TAG=$(echo "$RELEASE_JSON" | "$FRAMEWORK/venv/bin/python" -c "import json,sys;print(json.load(sys.stdin)['tag_name'])")
echo "release: $TAG"

# Pick the best matching asset
ASSET_URL=""
for pattern in "${VARIANT_PATTERNS[@]}"; do
    ASSET_URL=$(echo "$RELEASE_JSON" | "$FRAMEWORK/venv/bin/python" -c "
import json,sys,re
d=json.load(sys.stdin)
pat=re.compile(r'$pattern', re.IGNORECASE)
for a in d.get('assets',[]):
    if pat.search(a['name']) and a['name'].endswith(('.bin','.zip')):
        print(a['browser_download_url']); sys.exit(0)
")
    [ -n "$ASSET_URL" ] && break
done

[ -n "$ASSET_URL" ] || {
    echo "ERROR: no matching asset found. Available assets:"
    echo "$RELEASE_JSON" | "$FRAMEWORK/venv/bin/python" -c "
import json,sys
for a in json.load(sys.stdin).get('assets',[]): print(' -', a['name'])
"
    exit 2
}

FILENAME=$(basename "$ASSET_URL")
OUT="$FIRMWARE_DIR/$FILENAME"
echo "downloading $FILENAME ..."
curl -sSL -m 120 -o "$OUT" "$ASSET_URL"

# If it's a zip, unpack and find the .bin inside
if [[ "$FILENAME" == *.zip ]]; then
    (cd "$FIRMWARE_DIR" && unzip -o "$FILENAME" >/dev/null)
    INNER_BIN=$(find "$FIRMWARE_DIR" -name '*.bin' -newer "$OUT" | head -1)
    [ -n "$INNER_BIN" ] && cp "$INNER_BIN" "$FIRMWARE_DIR/NerdMinerV2_S3_devkit.bin"
else
    cp "$OUT" "$FIRMWARE_DIR/NerdMinerV2_S3_devkit.bin"
fi

echo "firmware pinned at: $FIRMWARE_DIR/NerdMinerV2_S3_devkit.bin"
ls -la "$FIRMWARE_DIR"/NerdMinerV2_S3_devkit.bin
sha256sum "$FIRMWARE_DIR/NerdMinerV2_S3_devkit.bin" > "$FIRMWARE_DIR/NerdMinerV2_S3_devkit.bin.sha256"
cat "$FIRMWARE_DIR/NerdMinerV2_S3_devkit.bin.sha256"
echo "release tag: $TAG" > "$FIRMWARE_DIR/RELEASE"
