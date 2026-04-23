#!/usr/bin/env bash
# Baza Empire — NerdMiner ESP32-S3 flasher
# Flashes a specific ESP32-S3 with NerdMinerV2 firmware + WiFi/pool/BTC config
# stored in configs/secrets.env. Idempotent — re-running on an already-flashed
# board reloads config without a full firmware rewrite (unless --full).
#
# Usage:
#   ./flash.sh --port /dev/ttyACM0 --worker 1 [--full] [--firmware /path/to.bin]
#
# Roster map (MAC → worker slot):
#   1c:db:d4:42:16:0c → worker 1 (baza-micro-node-1)
#   90:70:69:07:a6:0c → worker 2 (baza-micro-node-2)
#
# The board must be in ROM bootloader mode. Hold BOOT while plugging USB, then
# release after ~1s. VID:PID flips from 303a:4001 to 303a:1001 when ready.
set -euo pipefail

FRAMEWORK=/home/switchhacker/baza-empire/agent-framework-v3
FIRMWARE_DIR="$FRAMEWORK/firmware/nerdminer"
DEFAULT_FIRMWARE="$FIRMWARE_DIR/NerdMinerV2_S3_devkit.bin"
LOG="$FRAMEWORK/logs/nerdminer_flash.log"
PY="$FRAMEWORK/venv/bin/python"
ESPTOOL="$PY -m esptool"

# shellcheck disable=SC1091
[ -f "$FRAMEWORK/configs/secrets.env" ] && . "$FRAMEWORK/configs/secrets.env"

PORT=""
WORKER=""
FIRMWARE="$DEFAULT_FIRMWARE"
FULL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)     PORT="$2"; shift 2 ;;
        --worker)   WORKER="$2"; shift 2 ;;
        --firmware) FIRMWARE="$2"; shift 2 ;;
        --full)     FULL=1; shift ;;
        -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
        *)          echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -n "$PORT" ]   || { echo "ERROR: --port required"; exit 2; }
[ -n "$WORKER" ] || { echo "ERROR: --worker required (1 or 2)"; exit 2; }
[ -c "$PORT" ]   || { echo "ERROR: $PORT is not a character device"; exit 2; }

mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

# Resolve per-worker config
case "$WORKER" in
    1) WORKER_NAME="${NERDMINER_WORKER_1:-baza-micro-node-1}" ;;
    2) WORKER_NAME="${NERDMINER_WORKER_2:-baza-micro-node-2}" ;;
    *) echo "ERROR: --worker must be 1 or 2"; exit 2 ;;
esac

: "${NERDMINER_WIFI_SSID:?missing NERDMINER_WIFI_SSID — check secrets.env}"
: "${NERDMINER_WIFI_PSK:?missing NERDMINER_WIFI_PSK — check secrets.env}"
: "${NERDMINER_POOL_HOST:?missing NERDMINER_POOL_HOST — check secrets.env}"
: "${NERDMINER_POOL_PORT:?missing NERDMINER_POOL_PORT — check secrets.env}"
: "${NERDMINER_BTC_ADDRESS:?missing NERDMINER_BTC_ADDRESS — fill in secrets.env before flashing}"

log "=== flash $WORKER_NAME on $PORT (full=$FULL) ==="

# 1. Identify chip — confirms it's in ROM bootloader
sg dialout -c "$ESPTOOL --port $PORT --chip esp32s3 chip-id" 2>&1 | tee -a "$LOG" | grep -E 'MAC|Chip type' || true

# 2. Full firmware flash (only on first install or --full)
if [ "$FULL" = "1" ]; then
    [ -f "$FIRMWARE" ] || { echo "ERROR: firmware not found at $FIRMWARE"; exit 3; }
    log "erasing flash..."
    sg dialout -c "$ESPTOOL --port $PORT --chip esp32s3 erase-flash" 2>&1 | tee -a "$LOG"
    log "writing firmware $FIRMWARE..."
    sg dialout -c "$ESPTOOL --port $PORT --chip esp32s3 write-flash -z 0x0 $FIRMWARE" 2>&1 | tee -a "$LOG"
    log "firmware written"
fi

# 3. Build NVS config partition (WiFi + pool + BTC addr)
NVS_CSV="$FIRMWARE_DIR/nvs_${WORKER}.csv"
NVS_BIN="$FIRMWARE_DIR/nvs_${WORKER}.bin"
cat > "$NVS_CSV" <<EOF
key,type,encoding,value
storage,namespace,,
wifi_ssid,data,string,${NERDMINER_WIFI_SSID}
wifi_pass,data,string,${NERDMINER_WIFI_PSK}
pool_url,data,string,${NERDMINER_POOL_HOST}
pool_port,data,u32,${NERDMINER_POOL_PORT}
btc_addr,data,string,${NERDMINER_BTC_ADDRESS}
worker,data,string,${WORKER_NAME}
EOF
chmod 600 "$NVS_CSV"

# Use esp-idf's nvs_partition_gen if available, else nvs_gen.py from esptool contrib
if command -v nvs_partition_gen.py >/dev/null 2>&1; then
    nvs_partition_gen.py generate "$NVS_CSV" "$NVS_BIN" 0x6000
else
    "$PY" -c "
import nvs_partition_gen as g
g.nvs_part_gen(input_filename='$NVS_CSV', output_filename='$NVS_BIN', size='0x6000', encrypt='False')
" 2>/dev/null || {
        log "WARN: nvs_partition_gen unavailable — NerdMiner will fall back to captive-portal setup."
        log "      Connect phone to the 'NerdMinerAP-*' WiFi that appears, set config via web UI."
        rm -f "$NVS_BIN"
    }
fi

# 4. Write NVS partition at default offset 0x9000 (standard ESP-IDF layout)
if [ -f "$NVS_BIN" ]; then
    log "writing NVS config partition..."
    sg dialout -c "$ESPTOOL --port $PORT --chip esp32s3 write-flash 0x9000 $NVS_BIN" 2>&1 | tee -a "$LOG"
    log "NVS written — ${WORKER_NAME} pre-configured for WiFi + pool + payout"
fi

# 5. Hard reset so firmware boots with new config
sg dialout -c "$ESPTOOL --port $PORT --chip esp32s3 --after hard-reset run" 2>&1 | tail -3 | tee -a "$LOG" || true

log "=== done: $WORKER_NAME on $PORT ==="
echo
echo "Monitor live with:  sudo cu -l $PORT -s 115200  (or /dev/serial stats via skill)"
echo "Pool dashboard:      https://public-pool.io/worker/${NERDMINER_BTC_ADDRESS}"
