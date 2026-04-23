#!/usr/bin/env python3
"""
Baza Empire — NerdMiner Monitor (long-running systemd daemon)

Tails serial output from both ESP32-S3 NerdMiner nodes, parses hashrate / shares
/ uptime / temp, writes the latest snapshot to Postgres empire_knowledge under
keys `nerdminer:<worker>:status`, and alerts Simon via Telegram if a node drops
offline for >10 min or temperature exceeds threshold.

Run via baza-nerdminer-monitor.service. No-op on ports that don't exist.
"""
import os, sys, time, json, re, glob, logging, threading, urllib.request, urllib.parse

try:
    import serial
except ImportError:
    print("pyserial not installed", file=sys.stderr); sys.exit(1)

import psycopg2

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load env from secrets.env if systemd didn't
if not os.environ.get("NERDMINER_WIFI_SSID"):
    for line in open(os.path.join(FRAMEWORK, "configs", "secrets.env")):
        line = line.strip()
        if line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("nerdminer-monitor")

MAC_ROSTER = {
    "1c:db:d4:42:16:0c": os.environ.get("NERDMINER_WORKER_1", "baza-micro-node-1"),
    "90:70:69:07:a6:0c": os.environ.get("NERDMINER_WORKER_2", "baza-micro-node-2"),
}

OFFLINE_ALERT_SEC = int(os.environ.get("NERDMINER_OFFLINE_ALERT_SEC", "600"))
TEMP_ALERT_C = float(os.environ.get("NERDMINER_TEMP_ALERT_C", "75"))

TG_TOKEN = os.environ.get("TELEGRAM_SIMON_BATELY", "")
TG_CHAT = os.environ.get("SERGE_CHAT_ID", "")

STATE_LOCK = threading.Lock()
STATE = {}  # port → {worker, mac, hashrate_khs, shares_valid, ..., last_serial_at}
ALERTED = {}

PATTERNS = [
    (r'(?:Hashrate|\[Mining\] Hashrate)[:\s]+([\d.]+)\s*KH/s', 'hashrate_khs', float),
    (r'Valid\s*(?:shares)?[:\s]+(\d+)',        'shares_valid', int),
    (r'Invalid\s*(?:shares)?[:\s]+(\d+)',      'shares_invalid', int),
    (r'Blocks?\s*found[:\s]+(\d+)',            'blocks_found', int),
    (r'Uptime[:\s]+(\d+)s',                    'uptime_s', int),
    (r'Temperature[:\s]+([\d.]+)',             'temp_c', float),
    (r'Pool[:\s]+([\w.:-]+)',                  'pool', str),
    (r'Worker[:\s]+(\S+)',                     'worker_name', str),
    (r'MAC[:\s]+([0-9a-fA-F:]{17})',           'mac', str),
    (r'IP[:\s]+(\d+\.\d+\.\d+\.\d+)',          'ip', str),
    (r'WiFi[:\s]+(\w+)',                       'wifi_status', str),
]


def telegram(msg: str):
    if not (TG_TOKEN and TG_CHAT):
        log.warning("telegram alert skipped (missing TG creds): %s", msg)
        return
    try:
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=data, timeout=10,
        ).read()
    except Exception as e:
        log.warning("telegram send failed: %s", e)


def pg_write(worker: str, status: dict):
    try:
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            dbname="baza_agents",
            user=os.environ.get("DB_USER", "switchhacker"),
            password=os.environ.get("DB_PASSWORD", "baza2026"),
        )
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO empire_knowledge(key, value, category, updated_at, updated_by)
            VALUES(%s, %s, 'nerdminer', now(), 'nerdminer-monitor')
            ON CONFLICT (key) DO UPDATE
            SET value=EXCLUDED.value, updated_at=now(), updated_by='nerdminer-monitor'
            """,
            (f"nerdminer:{worker}:status", json.dumps(status, default=str)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("pg_write failed: %s", e)


def _open_no_reset(port: str):
    """Open the serial port WITHOUT toggling DTR/RTS — critical for ESP32-S3
    native USB-JTAG, which can otherwise interpret the toggle as a reset signal.
    Also disables HUPCL so close() doesn't drop carrier and reset the chip."""
    import termios, fcntl
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        # cflag: clear HUPCL so close doesn't trigger reset
        attrs[2] &= ~termios.HUPCL
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except Exception:
        pass
    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = 1.0
    s.dsrdtr = False
    s.rtscts = False
    s.xonxoff = False
    # Attach the already-opened fd to avoid a second open
    os.close(fd)
    s.open()
    # Ensure lines stay deasserted (no reset)
    try:
        s.setDTR(False); s.setRTS(False)
    except Exception:
        pass
    return s


def tail_port(port: str):
    """Long-running reader for one serial port. Reopens on disconnect with
    exponential backoff so we don't thrash the ESP32's USB stack."""
    backoff = 5
    while True:
        ser = None
        try:
            ser = _open_no_reset(port)
            log.info("opened %s (no-reset)", port)
            backoff = 5
            last_line_at = time.time()
            while True:
                try:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                except (OSError, serial.SerialException) as e:
                    log.info("%s read error: %s", port, e)
                    break
                now = time.time()
                if not line:
                    # Keepalive: once per 30s we note the port is still open even if silent
                    if now - last_line_at > 30:
                        last_line_at = now
                    continue
                last_line_at = now
                with STATE_LOCK:
                    st = STATE.setdefault(port, {"port": port})
                    for rx, key, cast in PATTERNS:
                        m = re.search(rx, line)
                        if m:
                            try: st[key] = cast(m.group(1))
                            except: pass
                    mac = (st.get("mac") or "").lower()
                    if mac in MAC_ROSTER:
                        st["worker"] = MAC_ROSTER[mac]
                    st["last_serial_at"] = now
                    st["last_line"] = line[:200]
                if st.get("hashrate_khs") is not None and st.get("worker"):
                    pg_write(st["worker"], dict(st))
        except Exception as e:
            log.warning("%s: %s — retrying in %ds", port, e, backoff)
        finally:
            try:
                if ser: ser.close()
            except Exception: pass
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def health_loop():
    """Alert on offline miners and overtemp."""
    while True:
        time.sleep(60)
        now = time.time()
        with STATE_LOCK:
            for port, st in STATE.items():
                worker = st.get("worker", port)
                last = st.get("last_serial_at", 0)
                silent = now - last if last else float("inf")
                # Offline alert
                if silent > OFFLINE_ALERT_SEC and not ALERTED.get(f"offline:{worker}"):
                    telegram(f"🛑 NerdMiner offline: {worker} silent for {int(silent/60)}m on {port}")
                    ALERTED[f"offline:{worker}"] = now
                elif silent < OFFLINE_ALERT_SEC / 2:
                    ALERTED.pop(f"offline:{worker}", None)
                # Overtemp
                temp = st.get("temp_c")
                if temp and temp > TEMP_ALERT_C and not ALERTED.get(f"temp:{worker}"):
                    telegram(f"🔥 NerdMiner overtemp: {worker} at {temp}°C (threshold {TEMP_ALERT_C}°C) on {port}")
                    ALERTED[f"temp:{worker}"] = now
                elif temp and temp < TEMP_ALERT_C - 5:
                    ALERTED.pop(f"temp:{worker}", None)


def main():
    ports = sorted(glob.glob("/dev/ttyACM*"))
    if not ports:
        log.error("no /dev/ttyACM* devices — exiting")
        sys.exit(0)
    log.info("monitoring: %s", ports)
    for p in ports:
        threading.Thread(target=tail_port, args=(p,), daemon=True, name=f"tail-{p}").start()
    threading.Thread(target=health_loop, daemon=True, name="health").start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
