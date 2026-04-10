#!/usr/bin/env python3
"""
Baza Empire — Edge TTS Voice Synthesis Skill
Uses Microsoft Edge TTS (free, 300+ natural voices) with SSML support
for human-style speech with pauses, prosody control, and natural rhythm.

SKILL_ARGS:
  text: "Hello, this is Rex from All Home Building Co..."
  voice: "en-US-GuyNeural"  (default)
  rate: "+10%"               (default: +0%)
  pitch: "+5Hz"              (default: +0Hz)
  volume: "+0%"              (default: +0%)
  humanize: true             (add natural pauses, rhythm variation)
  style: "friendly"          (friendly/professional/urgent/casual/empathetic)
  output_path: "/path/to/output.mp3"  (optional, auto-generated if omitted)
  list_voices: true          (if set, just list available voices and exit)
"""
import os
import sys
import json
import re
import uuid
import subprocess
import random

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENV_PYTHON = os.path.join(FRAMEWORK_DIR, "venv", "bin", "python")
VOICE_DIR = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts", "voice")
os.makedirs(VOICE_DIR, exist_ok=True)

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))

# ── List voices mode ─────────────────────────────────────────────────────────

if args.get("list_voices"):
    try:
        result = subprocess.run(
            [VENV_PYTHON, "-m", "edge_tts", "--list-voices"],
            capture_output=True, text=True, timeout=15
        )
        voices = []
        for line in result.stdout.strip().split("\n"):
            if line.startswith("Name:"):
                voice = {"name": line.split(":", 1)[1].strip()}
            elif line.startswith("Gender:"):
                voice["gender"] = line.split(":", 1)[1].strip()
                voices.append(voice)
                voice = {}
        # Group by language
        grouped = {}
        for v in voices:
            lang = v["name"].split("-")[0] + "-" + v["name"].split("-")[1] if "-" in v["name"] else "other"
            if lang not in grouped:
                grouped[lang] = []
            grouped[lang].append(v)
        print(json.dumps({"voices": voices, "grouped": grouped, "count": len(voices)}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
    sys.exit(0)

# ── Synthesis mode ───────────────────────────────────────────────────────────

text = args.get("text", "")
if not text:
    print(json.dumps({"error": "text is required"}))
    sys.exit(1)

voice = args.get("voice", "en-US-GuyNeural")
rate = args.get("rate", "+0%")
pitch = args.get("pitch", "+0Hz")
volume = args.get("volume", "+0%")
humanize = args.get("humanize", False)
style = args.get("style", "friendly")
output_path = args.get("output_path", "")

if not output_path:
    output_path = os.path.join(VOICE_DIR, f"tts_{uuid.uuid4().hex[:8]}.mp3")

# ── Humanize text with SSML-style modifications ──────────────────────────────

def humanize_text(text, style="friendly"):
    """Add natural pauses and rhythm variation to make speech sound more human."""
    # Add pauses at sentence boundaries
    text = re.sub(r'([.!?])\s+', r'\1 ... ', text)

    # Add shorter pauses at commas
    text = re.sub(r',\s+', ', .. ', text)

    # Add micro-pauses at semicolons and colons
    text = re.sub(r'[;:]\s+', lambda m: m.group() + '. ', text)

    # Style-specific modifications
    if style == "friendly":
        # Slightly slower, warmer
        pass  # rate adjustment happens at the voice level
    elif style == "urgent":
        # Remove extra pauses for urgency
        text = text.replace(' ... ', ' . ')
        text = text.replace(' .. ', ' ')
    elif style == "empathetic":
        # Longer pauses, more deliberate
        text = text.replace(' ... ', ' .... ')
    elif style == "casual":
        # Add filler-like pauses
        sentences = text.split('. ')
        result = []
        for i, s in enumerate(sentences):
            if i > 0 and random.random() > 0.6:
                result.append('.. ' + s)
            else:
                result.append(s)
        text = '. '.join(result)

    return text


if humanize:
    text = humanize_text(text, style)

# ── Style-based rate/pitch adjustments ───────────────────────────────────────

STYLE_ADJUSTMENTS = {
    "friendly":     {"rate_adj": "-5%",  "pitch_adj": "+2Hz"},
    "professional": {"rate_adj": "+0%",  "pitch_adj": "+0Hz"},
    "urgent":       {"rate_adj": "+15%", "pitch_adj": "+3Hz"},
    "casual":       {"rate_adj": "-8%",  "pitch_adj": "-1Hz"},
    "empathetic":   {"rate_adj": "-10%", "pitch_adj": "-2Hz"},
}

if style in STYLE_ADJUSTMENTS and rate == "+0%":
    rate = STYLE_ADJUSTMENTS[style]["rate_adj"]
if style in STYLE_ADJUSTMENTS and pitch == "+0Hz":
    pitch = STYLE_ADJUSTMENTS[style]["pitch_adj"]

# ── Run edge-tts ─────────────────────────────────────────────────────────────

cmd = [
    VENV_PYTHON, "-m", "edge_tts",
    "--voice", voice,
    "--rate", rate,
    "--pitch", pitch,
    "--volume", volume,
    "--text", text,
    "--write-media", output_path,
]

try:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        print(json.dumps({
            "success": False,
            "error": f"edge-tts failed: {result.stderr.strip()}",
            "cmd": " ".join(cmd[:6] + ['[text]', '--write-media', output_path]),
        }))
        sys.exit(1)

    file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
    filename = os.path.basename(output_path)

    print(f"Voice synthesis complete")
    print(f"  Voice: {voice}")
    print(f"  Style: {style}")
    print(f"  Rate: {rate} | Pitch: {pitch}")
    print(f"  Humanize: {humanize}")
    print(f"  Output: {filename} ({file_size} bytes)")
    print()
    print(json.dumps({
        "success": True,
        "voice": voice,
        "style": style,
        "rate": rate,
        "pitch": pitch,
        "humanize": humanize,
        "output_path": output_path,
        "filename": filename,
        "size": file_size,
        "audio_url": f"/api/ahb/voice/audio/{filename}",
    }))

except subprocess.TimeoutExpired:
    print(json.dumps({"success": False, "error": "edge-tts timed out (30s)"}))
except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
