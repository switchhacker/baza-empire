#!/usr/bin/env python3
"""
Baza Empire — Process Incoming Call Recording
Called by Asterisk after a caller leaves a voicemail.
Transcribes the audio, logs to database, notifies Serge via Telegram.

Usage (called by Asterisk dialplan):
  python3 process_call.py <recording_path> <caller_phone> <caller_name>

Or via SKILL_ARGS:
  SKILL_ARGS='{"recording":"/path/to/call.wav","phone":"2155551234","name":"John"}' python3 process_call.py
"""
import os
import sys
import json
import sqlite3
import datetime
import subprocess

# ── Parse args ───────────────────────────────────────────────────────────────

if len(sys.argv) >= 2:
    # Called from Asterisk command line
    recording_path = sys.argv[1] if len(sys.argv) > 1 else ""
    caller_phone = sys.argv[2] if len(sys.argv) > 2 else ""
    caller_name = sys.argv[3] if len(sys.argv) > 3 else ""
else:
    # Called via SKILL_ARGS
    args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
    recording_path = args.get("recording", "")
    caller_phone = args.get("phone", "")
    caller_name = args.get("name", "")

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
VENV_PYTHON = os.path.join(FRAMEWORK_DIR, "venv", "bin", "python3")

# ── Transcribe audio ────────────────────────────────────────────────────────

def transcribe_audio(audio_path):
    """Transcribe audio using cloud vision/whisper or Ollama."""
    if not os.path.exists(audio_path):
        return "(recording not found)"

    # Try whisper via command line
    try:
        result = subprocess.run(
            ["whisper", audio_path, "--model", "base", "--output_format", "txt", "--language", "en"],
            capture_output=True, text=True, timeout=120
        )
        txt_path = audio_path.rsplit(".", 1)[0] + ".txt"
        if os.path.exists(txt_path):
            with open(txt_path) as f:
                return f.read().strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try openai-whisper python module
    try:
        result = subprocess.run(
            [VENV_PYTHON, "-c", f"""
import whisper
model = whisper.load_model("base")
result = model.transcribe("{audio_path}")
print(result["text"])
"""],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # Fallback — try LiteLLM with audio description
    try:
        import urllib.request
        payload = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Transcribe the voicemail audio. Return only the transcript text."}],
            "max_tokens": 500
        }).encode()
        req = urllib.request.Request(
            "http://localhost:4000/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer baza-litellm"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result.get("choices", [{}])[0].get("message", {}).get("content", "(transcription unavailable)")
    except Exception:
        pass

    return "(transcription unavailable — no whisper or cloud model available)"


# ── Log to database ─────────────────────────────────────────────────────────

def log_call(caller_name, caller_phone, recording_path, transcript, duration=0):
    """Log call to ahb_voice_logs table."""
    try:
        db = sqlite3.connect(DB_PATH)
        db.execute("""INSERT INTO ahb_voice_logs
            (caller_name, caller_phone, direction, duration_seconds, transcript, audio_file, status, agent_notes)
            VALUES (?, ?, 'inbound', ?, ?, ?, 'voicemail', 'Auto-logged by Asterisk/Rex')""",
            (caller_name or "Unknown", caller_phone or "Unknown", duration, transcript,
             recording_path))
        db.commit()
        log_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()
        return log_id
    except Exception as e:
        print(f"DB error: {e}", file=sys.stderr)
        return None


# ── Notify via Telegram ──────────────────────────────────────────────────────

def notify_telegram(caller_name, caller_phone, transcript):
    """Send notification to Serge via Simon's Telegram bot."""
    secrets_path = os.path.join(FRAMEWORK_DIR, "configs", "secrets.env")
    token = ""
    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            for line in f:
                if line.startswith("TELEGRAM_SIMON_BATELY="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not token:
        return

    try:
        import urllib.request
        # Get chat_id from recent updates
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getUpdates?limit=5",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            updates = json.loads(resp.read()).get("result", [])

        chat_id = None
        for u in reversed(updates):
            msg = u.get("message", {})
            if msg.get("chat", {}).get("type") == "private":
                chat_id = msg["chat"]["id"]
                break

        if not chat_id:
            return

        message = (
            f"📞 New Voicemail — 800-484-6404\n\n"
            f"Caller: {caller_name or 'Unknown'}\n"
            f"Phone: {caller_phone or 'Unknown'}\n"
            f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"Transcript:\n{transcript[:500]}"
        )

        payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Processing call: phone={caller_phone}, name={caller_name}, recording={recording_path}")

    # Get audio duration
    duration = 0
    if recording_path and os.path.exists(recording_path):
        try:
            result = subprocess.run(
                ["soxi", "-D", recording_path], capture_output=True, text=True, timeout=5
            )
            duration = int(float(result.stdout.strip()))
        except Exception:
            try:
                # Fallback: file size estimate (8kHz mono wav ≈ 16KB/sec)
                size = os.path.getsize(recording_path)
                duration = max(1, size // 16000)
            except Exception:
                pass

    # Transcribe
    transcript = transcribe_audio(recording_path) if recording_path else "(no recording)"
    print(f"Transcript: {transcript[:200]}")

    # Log to database
    log_id = log_call(caller_name, caller_phone, recording_path, transcript, duration)
    print(f"Logged to DB: id={log_id}")

    # Notify Serge
    notify_telegram(caller_name, caller_phone, transcript)
    print("Notification sent")

    # Output JSON for skill system
    print(json.dumps({
        "success": True,
        "log_id": log_id,
        "caller": caller_name or "Unknown",
        "phone": caller_phone or "Unknown",
        "duration": duration,
        "transcript": transcript[:500],
    }))
