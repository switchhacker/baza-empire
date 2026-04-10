#!/usr/bin/env python3
"""Get GPU temp, memory, utilization for both GPUs."""
import os, json, subprocess

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
gpus = []

# NVIDIA GPU
try:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,temperature.gpu,memory.used,memory.total,utilization.gpu",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10)
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            parts = [p.strip() for p in line.split(",")]
            gpus.append({
                "type": "nvidia", "name": parts[0], "temp_c": int(parts[1]),
                "memory_used_mb": int(parts[2]), "memory_total_mb": int(parts[3]),
                "utilization_pct": int(parts[4])
            })
except Exception as e:
    gpus.append({"type": "nvidia", "status": "unavailable", "error": str(e)})

# AMD GPU via sensors
try:
    result = subprocess.run(["sensors"], capture_output=True, text=True, timeout=10)
    for line in result.stdout.split("\n"):
        if "amdgpu" in line.lower() or "edge" in line.lower():
            if "+" in line and "C" in line:
                temp = line.split("+")[1].split("C")[0].strip().replace("°", "")
                gpus.append({"type": "amd", "name": "RX 6700 XT", "temp_c": float(temp)})
                break
except Exception:
    pass

print(json.dumps({"gpus": gpus}))
