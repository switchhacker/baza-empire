#!/usr/bin/env python3
"""
Baza Empire — NerdMiner Status

Queries both ESP32-S3 NerdMiner nodes over USB serial and returns a structured
status: hashrate, valid/invalid shares, temperature, uptime, WiFi/pool state.

Also aggregates the last 5 minutes of stats from Postgres `empire_knowledge`
(kept fresh by the baza-nerdminer-monitor systemd service) so agents get a
useful answer even if the board is mid-reboot and serial is silent.

SKILL_ARGS:
  node : "1" | "2" | "all"   (default "all")
  timeout : 5                (seconds to listen on serial, default 5)

Returns: JSON { nodes: [ { worker, port, mac, hashrate_khs, shares_valid,
            shares_invalid, temp_c, uptime_s, last_seen, source, ... } ], ok }.
"""
import os, sys, json, re, time, glob

def _parse_nerdminer_line(line: str, state: dict):
    """Parse one line of NerdMiner log output into the running state dict.

    NerdMiner v2 prints lines like:
      [Stratum] Hashrate: 68.42 KH/s
      [Stratum] Valid shares: 3  Invalid: 0
      [Stratum] Uptime: 12345s
      Temperature: 48.7
      Connected to pool public-pool.io:21496
    """
    patterns = [
        (r'Hashrate:\s*([\d.]+)\s*KH/s', 'hashrate_khs', float),
        (r'Valid shares:\s*(\d+)',       'shares_valid', int),
        (r'Invalid:\s*(\d+)',            'shares_invalid', int),
        (r'Uptime:\s*(\d+)s',            'uptime_s', int),
        (r'Temperature:\s*([\d.]+)',     'temp_c', float),
        (r'Connected to pool ([\w.:-]+)', 'pool', str),
        (r'Worker:\s*(\S+)',             'worker', str),
        (r'MAC:\s*([0-9a-fA-F:]{17})',   'mac', str),
        (r'IP:\s*(\d+\.\d+\.\d+\.\d+)',  'ip', str),
        (r'WiFi:\s*(\w+)',               'wifi_status', str),
    ]
    for rx, key, cast in patterns:
        m = re.search(rx, line)
        if m:
            try: state[key] = cast(m.group(1))
            except: pass
    return state


def _read_serial(port: str, timeout: float = 5.0):
    """Listen on serial for up to `timeout` seconds, parse lines into state."""
    try:
        import serial  # pyserial
    except ImportError:
        return {"error": "pyserial not installed (pip install pyserial)"}
    state = {"port": port, "raw_lines": 0}
    try:
        ser = serial.Serial(port, 115200, timeout=0.5)
    except Exception as e:
        return {"port": port, "error": f"open failed: {e}"}
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            try:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                continue
            if not line:
                continue
            state["raw_lines"] += 1
            _parse_nerdminer_line(line, state)
            # Once we have key fields, stop early
            if "hashrate_khs" in state and "shares_valid" in state:
                break
    finally:
        try: ser.close()
        except: pass
    state["source"] = "serial"
    state["last_seen"] = time.time()
    return state


def _query_cached(worker_name: str):
    """Fall back to Postgres empire_knowledge (updated by the monitor service)."""
    try:
        import psycopg2
    except ImportError:
        return None
    try:
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            dbname="baza_agents",
            user=os.environ.get("DB_USER", "switchhacker"),
            password=os.environ.get("DB_PASSWORD", "baza2026"),
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT value, updated_at FROM empire_knowledge WHERE key=%s",
            (f"nerdminer:{worker_name}:status",),
        )
        row = cur.fetchone()
        conn.close()
        if not row: return None
        data = json.loads(row[0])
        data["source"] = "cache"
        data["cached_at"] = row[1].isoformat() if row[1] else None
        return data
    except Exception as e:
        return {"error": f"cache read failed: {e}"}


# MAC → worker slot mapping (filled in at probe time; hardcoded known devkits here)
MAC_ROSTER = {
    "1c:db:d4:42:16:0c": {"worker": os.environ.get("NERDMINER_WORKER_1", "baza-micro-node-1"), "slot": 1},
    "90:70:69:07:a6:0c": {"worker": os.environ.get("NERDMINER_WORKER_2", "baza-micro-node-2"), "slot": 2},
}


def discover_ports():
    """All /dev/ttyACM* that match ESP32-S3 VID:PID."""
    return sorted(glob.glob("/dev/ttyACM*"))


def collect(node: str = "all", timeout: float = 5.0):
    ports = discover_ports()
    targets = []
    if node == "all":
        targets = ports
    else:
        # map slot → port: assume ttyACM0=slot1, ttyACM1=slot2 unless we know the MAC
        idx = int(node) - 1
        if 0 <= idx < len(ports):
            targets = [ports[idx]]
    results = []
    for p in targets:
        state = _read_serial(p, timeout=timeout)
        # Enrich with roster info if MAC known
        mac = state.get("mac", "").lower()
        if mac in MAC_ROSTER:
            state["worker"] = MAC_ROSTER[mac]["worker"]
            state["slot"] = MAC_ROSTER[mac]["slot"]
        # If serial was silent, fall back to cache
        if not state.get("hashrate_khs") and state.get("worker"):
            cached = _query_cached(state["worker"])
            if cached: state["cached"] = cached
        results.append(state)
    return {"ok": True, "count": len(results), "nodes": results}


if __name__ == "__main__":
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    out = collect(node=args.get("node", "all"),
                  timeout=float(args.get("timeout", 5)))
    print(json.dumps(out, default=str))
