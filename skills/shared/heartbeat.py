#!/usr/bin/env python3
"""
Baza Empire — Heartbeat Skill
Writes an agent's alive signal to Redis with a 120-second TTL.
Called internally by BaseAgent's background loop every 60 seconds.

SKILL_ARGS: {"agent_id": "claw_batto", "model": "qwen2.5:14b", "status": "idle|busy"}
"""
import os
import json
import time

args     = json.loads(os.environ.get('SKILL_ARGS', '{}'))
agent_id = args.get('agent_id', os.environ.get('AGENT_ID', 'unknown'))
status   = args.get('status', 'idle')
model    = args.get('model', 'unknown')

try:
    import redis
    r = redis.Redis(host='localhost', port=6379, decode_responses=True)
    payload = json.dumps({
        "agent_id": agent_id,
        "model":    model,
        "status":   status,
        "ts":       int(time.time()),
        "ts_human": time.strftime("%H:%M:%S"),
    })
    r.setex(f"baza:heartbeat:{agent_id}", 120, payload)
    print(json.dumps({"success": True, "agent_id": agent_id, "ts": int(time.time())}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
