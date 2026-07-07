# Baza Flow Voice Dictation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only Python host daemon ("Baza Flow") that dictates speech into any focused window and provides two-way spoken conversation with Baza agents.

**Architecture:** A single long-lived systemd *user* process in the `agent-framework-v3` package, built from small independently-tested modules (config, hotkeys, recorder, stt, flow, commands, inject, agent_client, indicator, daemon). Speech is captured on a global hotkey, transcribed in-process by faster-whisper, optionally cleaned by local Ollama or routed to a Baza agent via the existing Fluid server, then injected into the focused window via clipboard-paste (xdotool/xclip). All engines already run on baza; this is a thin new daemon plus integration.

**Tech Stack:** Python 3.12, faster-whisper 1.2.1 (STT), Ollama `gemma4:12b-it-qat` (flow cleanup), Fluid server on `:8889` (agent turns + edge-tts), sounddevice/soundfile (capture), pynput (global hotkeys), pystray (tray), xdotool + xclip (X11 injection), pytest.

## Global Constraints

- **Local-first (HARD rule):** no cloud APIs in any mode. STT = faster-whisper in-process; flow cleanup = Ollama `gemma4:12b-it-qat`; agent replies + TTS = Fluid (edge-tts). No Anthropic/OpenAI/cloud keys anywhere.
- **Runtime:** shared venv at `/home/switchhacker/baza-empire/agent-framework-v3/venv` (Python 3.12.3). All commands run from repo root `/home/switchhacker/baza-empire/agent-framework-v3` unless stated.
- **Package root:** `voice_flow/` (new). Tests in `tests/voice_flow/`. Run tests with `venv/bin/pytest`.
- **Platform:** X11 session. Injection via `xdotool`; clipboard via `xclip` (`xsel` is NOT installed — do not use it). Playback via `aplay`/`ffplay`. All four binaries confirmed present.
- **Config file:** `voice_flow/config.yaml`. Every module reads settings from the `Config` object, never hardcodes hotkeys/models/URLs.
- **Default models/endpoints:** whisper `base` int8 CPU; Ollama `http://127.0.0.1:11434/api/generate`; Fluid `http://127.0.0.1:8889`; default agent `specter_voss`.
- **No manual git conflicts:** the repo is auto-committed hourly by `claw-auto-git`; per-task commits below are expected and coexist fine.
- **Style:** match existing repo modules (plain classes/functions, `logging` module, type hints where the codebase already uses them). No new heavyweight frameworks.

---

## File Structure

- Create `voice_flow/__init__.py` — package marker.
- Create `voice_flow/config.py` — `Config` dataclass + `load_config()` + hot-reload.
- Create `voice_flow/config.yaml` — default configuration.
- Create `voice_flow/recorder.py` — `Recorder` (mic capture) + `frames_to_wav()`.
- Create `voice_flow/stt.py` — `Transcriber` (faster-whisper in-process + Fluid fallback).
- Create `voice_flow/inject.py` — `Injector` (clipboard-paste save/restore + type fallback).
- Create `voice_flow/commands.py` — `match_command()` grammar + `Command` dataclass.
- Create `voice_flow/flow.py` — `clean_text()` Ollama cleanup pass.
- Create `voice_flow/agent_client.py` — `AgentClient` (Fluid say→stream→say_aloud) + `AgentReply`.
- Create `voice_flow/hotkeys.py` — `parse_chord()` + `HotkeyListener`.
- Create `voice_flow/indicator.py` — `Indicator` (tray + chimes), best-effort.
- Create `voice_flow/daemon.py` — `Daemon` state machine + `handle_utterance()` + `main()`.
- Create `tests/voice_flow/__init__.py` and `tests/voice_flow/test_*.py` per module.
- Create `voice_flow/README.md` — runbook (systemd unit, Handy coexistence flip).

Dependency note (Task 1 installs): `sounddevice`, `pynput`, `pystray`, `watchdog` are NOT yet in the venv. `faster-whisper`, `soundfile`, `requests`, `httpx`, `yaml` already present.

---

### Task 1: Package skeleton, dependencies, and config loader

**Files:**
- Create: `voice_flow/__init__.py`, `voice_flow/config.py`, `voice_flow/config.yaml`
- Create: `tests/voice_flow/__init__.py`, `tests/voice_flow/test_config.py`

**Interfaces:**
- Produces: `voice_flow.config.load_config(path: str | None = None) -> Config`. `Config` is a dataclass with attributes `hotkeys, stt, flow, agent, audio, injection, commands` (each a plain dict) plus `path: str` and method `reload_if_changed() -> bool` (returns True and re-reads if the file mtime changed).

- [ ] **Step 1: Install missing dependencies**

Run:
```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
venv/bin/pip install sounddevice pynput pystray watchdog
```
Expected: all four install successfully. (`sounddevice` needs system PortAudio, already present via `aplay`/ALSA; if it errors on `libportaudio2`, run `sudo apt-get install -y libportaudio2` and retry.)

- [ ] **Step 2: Create package markers**

Create `voice_flow/__init__.py`:
```python
"""Baza Flow — local voice dictation + agent voice comms daemon."""
```
Create `tests/voice_flow/__init__.py` (empty file).

- [ ] **Step 3: Write the default config file**

Create `voice_flow/config.yaml`:
```yaml
hotkeys:
  raw: "ctrl+space"
  flow: "ctrl+shift+space"
  agent: "ctrl+alt+space"
  cancel: "esc"
  mode: "hold"            # hold | toggle
stt:
  model: "base"
  compute_type: "int8"
  device: "cpu"
  fluid_stt_fallback: "http://127.0.0.1:8889/api/fluid/stt"
flow:
  ollama_url: "http://127.0.0.1:11434/api/generate"
  model: "gemma4:12b-it-qat"
  temperature: 0
  system_prompt: |
    Rewrite the dictated text with correct punctuation, capitalization and
    paragraphing. Remove filler words (um, uh, like). Preserve meaning and
    wording. Output only the cleaned text, nothing else.
agent:
  fluid_url: "http://127.0.0.1:8889"
  default_agent: "specter_voss"
  speak_reply: true
  type_reply: false
audio:
  input_device: null
  samplerate: 16000
  chimes: true
injection:
  method: "paste"          # paste | type
commands:
  enabled: true
```

- [ ] **Step 4: Write the failing test**

Create `tests/voice_flow/test_config.py`:
```python
import textwrap
from voice_flow.config import load_config


def test_load_defaults_from_packaged_yaml():
    cfg = load_config()  # no path → packaged voice_flow/config.yaml
    assert cfg.hotkeys["raw"] == "ctrl+space"
    assert cfg.stt["model"] == "base"
    assert cfg.agent["default_agent"] == "specter_voss"
    assert cfg.injection["method"] == "paste"


def test_load_custom_path(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        hotkeys: {raw: "alt+d"}
        stt: {model: "small"}
        flow: {}
        agent: {}
        audio: {}
        injection: {method: "type"}
        commands: {}
    """))
    cfg = load_config(str(p))
    assert cfg.hotkeys["raw"] == "alt+d"
    assert cfg.injection["method"] == "type"


def test_reload_if_changed(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("hotkeys: {raw: a}\nstt: {}\nflow: {}\nagent: {}\naudio: {}\ninjection: {}\ncommands: {}\n")
    cfg = load_config(str(p))
    assert cfg.reload_if_changed() is False
    import os, time
    time.sleep(0.01)
    p.write_text("hotkeys: {raw: b}\nstt: {}\nflow: {}\nagent: {}\naudio: {}\ninjection: {}\ncommands: {}\n")
    os.utime(str(p), None)
    assert cfg.reload_if_changed() is True
    assert cfg.hotkeys["raw"] == "b"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `venv/bin/pytest tests/voice_flow/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_flow.config'`

- [ ] **Step 6: Implement the config loader**

Create `voice_flow/config.py`:
```python
"""Configuration loading with mtime-based hot reload."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
import yaml

_DEFAULT_PATH = str(Path(__file__).with_name("config.yaml"))
_SECTIONS = ("hotkeys", "stt", "flow", "agent", "audio", "injection", "commands")


@dataclass
class Config:
    path: str
    _mtime: float
    hotkeys: dict = field(default_factory=dict)
    stt: dict = field(default_factory=dict)
    flow: dict = field(default_factory=dict)
    agent: dict = field(default_factory=dict)
    audio: dict = field(default_factory=dict)
    injection: dict = field(default_factory=dict)
    commands: dict = field(default_factory=dict)

    def reload_if_changed(self) -> bool:
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            return False
        if m == self._mtime:
            return False
        data = _read(self.path)
        for s in _SECTIONS:
            setattr(self, s, data.get(s, {}) or {})
        self._mtime = m
        return True


def _read(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def load_config(path: str | None = None) -> Config:
    path = path or _DEFAULT_PATH
    data = _read(path)
    return Config(
        path=path,
        _mtime=os.path.getmtime(path),
        **{s: (data.get(s, {}) or {}) for s in _SECTIONS},
    )
```

- [ ] **Step 7: Run test to verify it passes**

Run: `venv/bin/pytest tests/voice_flow/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Commit**

```bash
git add voice_flow/__init__.py voice_flow/config.py voice_flow/config.yaml tests/voice_flow/
git commit -m "feat(voice_flow): package skeleton + config loader with hot reload"
```

---

### Task 2: Audio recorder

**Files:**
- Create: `voice_flow/recorder.py`
- Test: `tests/voice_flow/test_recorder.py`

**Interfaces:**
- Consumes: `Config.audio` (`input_device`, `samplerate`).
- Produces:
  - `voice_flow.recorder.frames_to_wav(frames: "np.ndarray", samplerate: int, path: str) -> str` — writes mono 16-bit WAV, returns `path`.
  - `voice_flow.recorder.Recorder(device=None, samplerate=16000)` with `.start() -> None`, `.stop() -> str` (returns WAV path in a temp dir), `.abort() -> None` (discards, returns nothing). `.stop()` on an empty capture returns a valid short/silent WAV path (never raises).

- [ ] **Step 1: Write the failing test**

Create `tests/voice_flow/test_recorder.py`:
```python
import numpy as np
import soundfile as sf
from voice_flow.recorder import frames_to_wav


def test_frames_to_wav_writes_readable_mono(tmp_path):
    frames = (np.sin(np.linspace(0, 20, 16000)) * 0.2).astype("float32")
    out = tmp_path / "a.wav"
    path = frames_to_wav(frames, 16000, str(out))
    assert path == str(out)
    data, sr = sf.read(path)
    assert sr == 16000
    assert data.shape[0] == 16000
    assert data.ndim == 1


def test_frames_to_wav_empty_is_safe(tmp_path):
    out = tmp_path / "e.wav"
    path = frames_to_wav(np.zeros(0, dtype="float32"), 16000, str(out))
    data, sr = sf.read(path)
    assert sr == 16000
    assert data.shape[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/voice_flow/test_recorder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_flow.recorder'`

- [ ] **Step 3: Implement the recorder**

Create `voice_flow/recorder.py`:
```python
"""Press-to-talk mic capture → 16 kHz mono WAV."""
from __future__ import annotations
import os
import queue
import tempfile
import numpy as np
import soundfile as sf


def frames_to_wav(frames: np.ndarray, samplerate: int, path: str) -> str:
    if frames.ndim > 1:
        frames = frames.reshape(-1)
    sf.write(path, frames.astype("float32"), samplerate, subtype="PCM_16")
    return path


class Recorder:
    def __init__(self, device=None, samplerate: int = 16000):
        self.device = device
        self.samplerate = samplerate
        self._q: "queue.Queue" = queue.Queue()
        self._stream = None
        self._dir = tempfile.mkdtemp(prefix="baza-flow-")

    def _callback(self, indata, frames, time_info, status):  # sounddevice
        self._q.put(indata.copy())

    def start(self) -> None:
        import sounddevice as sd
        self._q = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=self.samplerate, channels=1, dtype="float32",
            device=self.device, callback=self._callback,
        )
        self._stream.start()

    def _drain(self) -> np.ndarray:
        chunks = []
        while not self._q.empty():
            chunks.append(self._q.get())
        if not chunks:
            return np.zeros(0, dtype="float32")
        return np.concatenate(chunks).reshape(-1)

    def stop(self) -> str:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        frames = self._drain()
        path = os.path.join(self._dir, "utt.wav")
        return frames_to_wav(frames, self.samplerate, path)

    def abort(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._drain()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/voice_flow/test_recorder.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add voice_flow/recorder.py tests/voice_flow/test_recorder.py
git commit -m "feat(voice_flow): mic recorder + frames_to_wav"
```

---

### Task 3: Speech-to-text (faster-whisper in-process + Fluid fallback)

**Files:**
- Create: `voice_flow/stt.py`
- Test: `tests/voice_flow/test_stt.py`

**Interfaces:**
- Consumes: `Config.stt` (`model`, `compute_type`, `device`, `fluid_stt_fallback`).
- Produces: `voice_flow.stt.Transcriber(model="base", compute_type="int8", device="cpu", fallback_url=None)` with `.transcribe(wav_path: str) -> str`. Loads the whisper model lazily on first call and keeps it warm. If model load raises and `fallback_url` is set, POSTs the WAV as multipart `audio` to the fallback and returns its JSON `text`.

- [ ] **Step 1: Write the failing test**

Create `tests/voice_flow/test_stt.py`:
```python
from unittest.mock import MagicMock, patch
from voice_flow.stt import Transcriber


def test_transcribe_in_process_concatenates_segments():
    seg = [MagicMock(text=" hello"), MagicMock(text=" world")]
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter(seg), MagicMock())
    t = Transcriber(model="base")
    with patch("voice_flow.stt.WhisperModel", return_value=fake_model) as WM:
        out = t.transcribe("/tmp/x.wav")
    WM.assert_called_once()
    fake_model.transcribe.assert_called_once_with("/tmp/x.wav")
    assert out == "hello world"


def test_transcribe_falls_back_to_fluid_on_load_error():
    t = Transcriber(model="base", fallback_url="http://fluid/stt")
    resp = MagicMock()
    resp.json.return_value = {"text": "fallback text"}
    with patch("voice_flow.stt.WhisperModel", side_effect=RuntimeError("no model")), \
         patch("voice_flow.stt.requests.post", return_value=resp) as post, \
         patch("builtins.open", MagicMock()):
        out = t.transcribe("/tmp/x.wav")
    assert out == "fallback text"
    assert post.call_args.args[0] == "http://fluid/stt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/voice_flow/test_stt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_flow.stt'`

- [ ] **Step 3: Implement the transcriber**

Create `voice_flow/stt.py`:
```python
"""Local faster-whisper STT with Fluid HTTP fallback."""
from __future__ import annotations
import logging
import requests
from faster_whisper import WhisperModel

log = logging.getLogger("voice_flow.stt")


class Transcriber:
    def __init__(self, model="base", compute_type="int8", device="cpu", fallback_url=None):
        self._name = model
        self._compute_type = compute_type
        self._device = device
        self._fallback_url = fallback_url
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            self._model = WhisperModel(
                self._name, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, wav_path: str) -> str:
        try:
            model = self._ensure_model()
        except Exception as e:  # noqa: BLE001
            if self._fallback_url:
                log.warning("whisper load failed (%s); using Fluid fallback", e)
                return self._fallback(wav_path)
            raise
        segments, _info = model.transcribe(wav_path)
        return "".join(s.text for s in segments).strip()

    def _fallback(self, wav_path: str) -> str:
        with open(wav_path, "rb") as f:
            resp = requests.post(
                self._fallback_url, files={"audio": ("utt.wav", f, "audio/wav")}, timeout=60
            )
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/voice_flow/test_stt.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add voice_flow/stt.py tests/voice_flow/test_stt.py
git commit -m "feat(voice_flow): faster-whisper STT with Fluid fallback"
```

---

### Task 4: Text injection (clipboard-paste with save/restore + type fallback)

**Files:**
- Create: `voice_flow/inject.py`
- Test: `tests/voice_flow/test_inject.py`

**Interfaces:**
- Consumes: `Config.injection` (`method`: `"paste"` | `"type"`).
- Produces: `voice_flow.inject.Injector(method="paste", runner=subprocess.run, restore_delay=0.15)` with:
  - `.inject(text: str) -> int` — puts `text` into the focused window; returns the number of characters injected (used by `scratch that`).
  - `.press(keys: str) -> None` — sends an xdotool key chord, e.g. `"Return"`, `"ctrl+a"`.
  - `.delete_last(n: int) -> None` — sends `n` BackSpace keystrokes.
  - Paste method: read current clipboard via `xclip -o -selection clipboard`, set new via `xclip -i -selection clipboard`, `xdotool key ctrl+v`, then restore prior clipboard after `restore_delay`. `runner` is injected for testability.

- [ ] **Step 1: Write the failing test**

Create `tests/voice_flow/test_inject.py`:
```python
from unittest.mock import MagicMock
from voice_flow.inject import Injector


def _runner_recording():
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        m = MagicMock()
        m.stdout = b"OLD_CLIP"
        m.returncode = 0
        return m
    return run, calls


def test_paste_sets_clipboard_and_sends_ctrl_v():
    run, calls = _runner_recording()
    inj = Injector(method="paste", runner=run, restore_delay=0)
    n = inj.inject("hello world")
    assert n == len("hello world")
    argvs = [c[0] for c in calls]
    # clipboard read, clipboard write (new), ctrl+v, clipboard restore
    assert any("xclip" in a and "-o" in a for a in argvs)
    assert any(a[:3] == ["xdotool", "key", "ctrl+v"] for a in argvs)


def test_type_method_uses_xdotool_type():
    run, calls = _runner_recording()
    inj = Injector(method="type", runner=run)
    inj.inject("hi")
    argvs = [c[0] for c in calls]
    assert any(a[:2] == ["xdotool", "type"] and a[-1] == "hi" for a in argvs)


def test_delete_last_sends_backspaces():
    run, calls = _runner_recording()
    inj = Injector(method="type", runner=run)
    inj.delete_last(3)
    argvs = [c[0] for c in calls]
    assert ["xdotool", "key", "--repeat", "3", "BackSpace"] in argvs


def test_press_sends_key_chord():
    run, calls = _runner_recording()
    inj = Injector(method="type", runner=run)
    inj.press("Return")
    assert ["xdotool", "key", "Return"] in [c[0] for c in calls]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/voice_flow/test_inject.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'voice_flow.inject'`

- [ ] **Step 3: Implement the injector**

Create `voice_flow/inject.py`:
```python
"""Inject text into the focused X11 window via xdotool/xclip."""
from __future__ import annotations
import subprocess
import time

_CLIP = ["xclip", "-selection", "clipboard"]


class Injector:
    def __init__(self, method: str = "paste", runner=subprocess.run, restore_delay: float = 0.15):
        self.method = method
        self._run = runner
        self._restore_delay = restore_delay

    def inject(self, text: str) -> int:
        if not text:
            return 0
        if self.method == "type":
            self._run(["xdotool", "type", "--clearmodifiers", "--", text])
        else:
            self._paste(text)
        return len(text)

    def _paste(self, text: str) -> None:
        prev = None
        try:
            r = self._run(_CLIP + ["-o"], capture_output=True)
            prev = getattr(r, "stdout", None)
        except Exception:  # noqa: BLE001
            prev = None
        self._run(_CLIP + ["-i"], input=text.encode())
        self._run(["xdotool", "key", "ctrl+v"])
        if prev is not None:
            time.sleep(self._restore_delay)
            self._run(_CLIP + ["-i"], input=prev if isinstance(prev, bytes) else str(prev).encode())

    def press(self, keys: str) -> None:
        self._run(["xdotool", "key", keys])

    def delete_last(self, n: int) -> None:
        if n > 0:
            self._run(["xdotool", "key", "--repeat", str(n), "BackSpace"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/voice_flow/test_inject.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add voice_flow/inject.py tests/voice_flow/test_inject.py
git commit -m "feat(voice_flow): clipboard-paste injector with save/restore"
```

---

### Task 5: Hotkeys + daemon raw-dictation milestone

**Files:**
- Create: `voice_flow/hotkeys.py`, `voice_flow/daemon.py`
- Test: `tests/voice_flow/test_hotkeys.py`, `tests/voice_flow/test_daemon_raw.py`

**Interfaces:**
- Produces:
  - `voice_flow.hotkeys.parse_chord(s: str) -> frozenset[str]` — normalizes `"Ctrl+Space"` → `frozenset({"ctrl", "space"})`.
  - `voice_flow.hotkeys.HotkeyListener(bindings: dict[str, str], on_press, on_release)` — wraps pynput; `bindings` maps chord string → mode name; `on_press(mode)`/`on_release(mode)` callbacks. `.start()`/`.stop()`. (Not unit-tested for live keys; only `parse_chord` is.)
  - `voice_flow.daemon.Daemon(config, transcriber, injector, recorder_factory, flow_fn=None, agent_client=None, commands_enabled=True, indicator=None)` with `.handle_utterance(mode: str, wav_path: str) -> str` returning the text that was injected/spoken (or "" for a handled command). `mode` ∈ `{"raw","flow","agent"}`. This method is the deterministic seam the tests drive with a fixture WAV.

- [ ] **Step 1: Write the failing tests**

Create `tests/voice_flow/test_hotkeys.py`:
```python
from voice_flow.hotkeys import parse_chord


def test_parse_chord_normalizes():
    assert parse_chord("Ctrl+Space") == frozenset({"ctrl", "space"})
    assert parse_chord("ctrl+shift+space") == frozenset({"ctrl", "shift", "space"})
    assert parse_chord("esc") == frozenset({"esc"})
```

Create `tests/voice_flow/test_daemon_raw.py`:
```python
from unittest.mock import MagicMock
from voice_flow.config import load_config
from voice_flow.daemon import Daemon


def _daemon(**over):
    cfg = load_config()
    tr = MagicMock(); tr.transcribe.return_value = "hello there"
    inj = MagicMock(); inj.inject.return_value = len("hello there")
    d = Daemon(config=cfg, transcriber=tr, injector=inj,
               recorder_factory=MagicMock(), commands_enabled=False, **over)
    return d, tr, inj


def test_raw_mode_transcribes_and_injects():
    d, tr, inj = _daemon()
    out = d.handle_utterance("raw", "/tmp/x.wav")
    tr.transcribe.assert_called_once_with("/tmp/x.wav")
    inj.inject.assert_called_once_with("hello there")
    assert out == "hello there"


def test_empty_transcript_injects_nothing():
    d, tr, inj = _daemon()
    tr.transcribe.return_value = "   "
    out = d.handle_utterance("raw", "/tmp/x.wav")
    inj.inject.assert_not_called()
    assert out == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/voice_flow/test_hotkeys.py tests/voice_flow/test_daemon_raw.py -v`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement hotkeys**

Create `voice_flow/hotkeys.py`:
```python
"""Global hotkey parsing + pynput listener."""
from __future__ import annotations
import logging

log = logging.getLogger("voice_flow.hotkeys")

_ALIASES = {"control": "ctrl", "escape": "esc", "spacebar": "space"}


def parse_chord(s: str) -> frozenset[str]:
    parts = [p.strip().lower() for p in s.split("+") if p.strip()]
    return frozenset(_ALIASES.get(p, p) for p in parts)


class HotkeyListener:
    def __init__(self, bindings: dict[str, str], on_press, on_release):
        self._bindings = {parse_chord(c): mode for c, mode in bindings.items()}
        self._on_press = on_press
        self._on_release = on_release
        self._down: set[str] = set()
        self._active: str | None = None
        self._listener = None

    def _norm(self, key) -> str | None:
        from pynput import keyboard
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char.lower()
        if isinstance(key, keyboard.Key):
            name = key.name.lower()
            if name.startswith("ctrl"): return "ctrl"
            if name.startswith("shift"): return "shift"
            if name.startswith("alt"): return "alt"
            return _ALIASES.get(name, name)
        return None

    def _press(self, key):
        n = self._norm(key)
        if n is None:
            return
        self._down.add(n)
        for chord, mode in self._bindings.items():
            if chord <= self._down and self._active is None:
                self._active = mode
                self._on_press(mode)

    def _release(self, key):
        n = self._norm(key)
        if n is None:
            return
        if self._active is not None:
            mode = self._active
            self._active = None
            self._on_release(mode)
        self._down.discard(n)

    def start(self):
        from pynput import keyboard
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None
```

- [ ] **Step 4: Implement the daemon (raw path only for now)**

Create `voice_flow/daemon.py`:
```python
"""Baza Flow daemon: mode state machine + utterance handling."""
from __future__ import annotations
import logging

log = logging.getLogger("voice_flow.daemon")


class Daemon:
    def __init__(self, config, transcriber, injector, recorder_factory,
                 flow_fn=None, agent_client=None, commands_enabled=True, indicator=None):
        self.cfg = config
        self.transcriber = transcriber
        self.injector = injector
        self.recorder_factory = recorder_factory
        self.flow_fn = flow_fn
        self.agent_client = agent_client
        self.commands_enabled = commands_enabled
        self.indicator = indicator
        self._recorder = None
        self._last_injected = 0
        self.active_dictation_mode = "raw"

    # --- hotkey callbacks (wired in main()) ---
    def on_press(self, mode: str) -> None:
        if mode == "cancel":
            self._abort(); return
        self._set_state("listening")
        self._recorder = self.recorder_factory()
        self._recorder.start()

    def on_release(self, mode: str) -> None:
        if mode == "cancel" or self._recorder is None:
            return
        wav = self._recorder.stop()
        self._recorder = None
        self._set_state("thinking")
        try:
            self.handle_utterance(mode, wav)
        finally:
            self._set_state("idle")

    def _abort(self) -> None:
        if self._recorder is not None:
            self._recorder.abort()
            self._recorder = None
        self._set_state("idle")

    def _set_state(self, s: str) -> None:
        if self.indicator is not None:
            self.indicator.set_state(s)

    # --- deterministic core (tested) ---
    def handle_utterance(self, mode: str, wav_path: str) -> str:
        text = (self.transcriber.transcribe(wav_path) or "").strip()
        if not text:
            return ""
        # command interception happens in Task 6 (guarded by commands_enabled)
        if mode == "agent" and self.agent_client is not None:
            return self._do_agent(text)
        if mode == "flow" and self.flow_fn is not None:
            text = self.flow_fn(text) or text
        self._last_injected = self.injector.inject(text)
        return text

    def _do_agent(self, text: str) -> str:
        reply = self.agent_client.ask(text)
        if self.cfg.agent.get("speak_reply", True):
            self.agent_client.speak(reply.text, reply.agent_id)
        if self.cfg.agent.get("type_reply", False):
            self.injector.inject(reply.text)
        return reply.text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/voice_flow/test_hotkeys.py tests/voice_flow/test_daemon_raw.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add voice_flow/hotkeys.py voice_flow/daemon.py tests/voice_flow/test_hotkeys.py tests/voice_flow/test_daemon_raw.py
git commit -m "feat(voice_flow): hotkey listener + daemon raw-dictation core"
```

---

### Task 6: Voice command grammar

**Files:**
- Create: `voice_flow/commands.py`
- Modify: `voice_flow/daemon.py` (wire command interception into `handle_utterance`)
- Test: `tests/voice_flow/test_commands.py`, `tests/voice_flow/test_daemon_commands.py`

**Interfaces:**
- Produces:
  - `voice_flow.commands.Command` dataclass: `action: str`, `arg: str | None = None`.
  - `voice_flow.commands.match_command(text: str, agents: list[str]) -> Command | None`. Returns `None` when the text is normal dictation. Recognized (anchored, case/punct-insensitive on the *whole* utterance): `"new line"`→`Command("newline")`, `"new paragraph"`→`Command("paragraph")`, `"scratch that"`→`Command("scratch")`, `"select all"`→`Command("select_all")`, `"undo that"`→`Command("undo")`, `"stop listening"`→`Command("stop")`, `"switch to flow"`→`Command("set_mode","flow")`, `"switch to raw"`→`Command("set_mode","raw")`, `"send to <agent>"`→`Command("route","<agent>")` when `<agent>` matches an entry in `agents` (case-insensitive prefix on first name).
- Consumes (daemon): `Config.commands["enabled"]`; agents list from `Config` (hardcode the 9 first names via a module constant `AGENT_NAMES`).

- [ ] **Step 1: Write the failing tests**

Create `tests/voice_flow/test_commands.py`:
```python
from voice_flow.commands import match_command, Command

AGENTS = ["specter", "simon", "claw", "phil", "sam", "rex", "duke", "scout", "nova"]


def test_editing_commands():
    assert match_command("new line", AGENTS) == Command("newline")
    assert match_command("New Line.", AGENTS) == Command("newline")
    assert match_command("scratch that", AGENTS) == Command("scratch")
    assert match_command("select all", AGENTS) == Command("select_all")


def test_mode_and_route():
    assert match_command("switch to flow", AGENTS) == Command("set_mode", "flow")
    assert match_command("send to Specter", AGENTS) == Command("route", "specter")
    assert match_command("send to nova", AGENTS) == Command("route", "nova")


def test_unknown_agent_is_not_a_command():
    assert match_command("send to grandma", AGENTS) is None


def test_normal_dictation_passes_through():
    assert match_command("let us schedule a new line item for the invoice", AGENTS) is None
    assert match_command("the weather is nice today", AGENTS) is None
```

Create `tests/voice_flow/test_daemon_commands.py`:
```python
from unittest.mock import MagicMock
from voice_flow.config import load_config
from voice_flow.daemon import Daemon


def _daemon():
    cfg = load_config()
    tr = MagicMock(); inj = MagicMock(); inj.inject.return_value = 5
    return Daemon(config=cfg, transcriber=tr, injector=inj,
                  recorder_factory=MagicMock(), commands_enabled=True), tr, inj


def test_new_line_command_presses_return_not_types():
    d, tr, inj = _daemon()
    tr.transcribe.return_value = "new line"
    out = d.handle_utterance("raw", "/tmp/x.wav")
    inj.press.assert_called_once_with("Return")
    inj.inject.assert_not_called()
    assert out == ""


def test_switch_to_flow_changes_active_mode():
    d, tr, inj = _daemon()
    tr.transcribe.return_value = "switch to flow"
    d.handle_utterance("raw", "/tmp/x.wav")
    assert d.active_dictation_mode == "flow"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/voice_flow/test_commands.py tests/voice_flow/test_daemon_commands.py -v`
Expected: FAIL — `voice_flow.commands` missing; daemon has no command handling.

- [ ] **Step 3: Implement the command grammar**

Create `voice_flow/commands.py`:
```python
"""Spoken meta-command grammar."""
from __future__ import annotations
import re
from dataclasses import dataclass

AGENT_NAMES = ["specter", "simon", "claw", "phil", "sam", "rex", "duke", "scout", "nova"]


@dataclass(frozen=True)
class Command:
    action: str
    arg: str | None = None


_STATIC = {
    "new line": Command("newline"),
    "new paragraph": Command("paragraph"),
    "scratch that": Command("scratch"),
    "select all": Command("select_all"),
    "undo that": Command("undo"),
    "stop listening": Command("stop"),
    "switch to flow": Command("set_mode", "flow"),
    "switch to raw": Command("set_mode", "raw"),
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.strip().lower()).strip()


def match_command(text: str, agents: list[str]) -> Command | None:
    n = _norm(text)
    if n in _STATIC:
        return _STATIC[n]
    m = re.fullmatch(r"send to (\w+)", n)
    if m:
        who = m.group(1)
        for a in agents:
            if a.lower().startswith(who) or who.startswith(a.lower()):
                return Command("route", a.lower())
    return None
```

- [ ] **Step 4: Wire command interception into the daemon**

In `voice_flow/daemon.py`, add the import at top:
```python
from voice_flow.commands import match_command, AGENT_NAMES
```
Replace the `handle_utterance` method body's comment line `# command interception happens in Task 6 ...` and the block after it with:
```python
        if self.commands_enabled:
            cmd = match_command(text, AGENT_NAMES)
            if cmd is not None:
                self._run_command(cmd)
                return ""
        effective = self.active_dictation_mode if mode in ("raw", "flow") else mode
        if effective == "agent" and self.agent_client is not None:
            return self._do_agent(text)
        if effective == "flow" and self.flow_fn is not None:
            text = self.flow_fn(text) or text
        self._last_injected = self.injector.inject(text)
        return text
```
Then add this method to the `Daemon` class:
```python
    def _run_command(self, cmd) -> None:
        if cmd.action == "newline":
            self.injector.press("Return")
        elif cmd.action == "paragraph":
            self.injector.press("Return"); self.injector.press("Return")
        elif cmd.action == "scratch":
            self.injector.delete_last(self._last_injected); self._last_injected = 0
        elif cmd.action == "select_all":
            self.injector.press("ctrl+a")
        elif cmd.action == "undo":
            self.injector.press("ctrl+z")
        elif cmd.action == "set_mode":
            self.active_dictation_mode = cmd.arg
        elif cmd.action == "route":
            self._pending_agent = cmd.arg
        elif cmd.action == "stop":
            self._abort()
```
(Delete the old `if mode == "agent" ...` / `if mode == "flow" ...` / `self.injector.inject` lines that the replacement above supersedes, so the method has a single return path.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/voice_flow/test_commands.py tests/voice_flow/test_daemon_commands.py tests/voice_flow/test_daemon_raw.py -v`
Expected: PASS (all — raw-dictation tests must still pass).

- [ ] **Step 6: Commit**

```bash
git add voice_flow/commands.py voice_flow/daemon.py tests/voice_flow/test_commands.py tests/voice_flow/test_daemon_commands.py
git commit -m "feat(voice_flow): spoken command grammar + daemon interception"
```

---

### Task 7: Flow cleanup pass (Ollama)

**Files:**
- Create: `voice_flow/flow.py`
- Test: `tests/voice_flow/test_flow.py`

**Interfaces:**
- Consumes: `Config.flow` (`ollama_url`, `model`, `temperature`, `system_prompt`).
- Produces: `voice_flow.flow.make_flow(cfg_flow: dict, poster=requests.post) -> callable`. The returned `flow_fn(text: str) -> str` POSTs to Ollama `/api/generate` with `{model, prompt, system, stream: false, options: {temperature}}` and returns the cleaned `response`. On any HTTP error, returns the original `text` unchanged (never raises into the hot path).

- [ ] **Step 1: Write the failing test**

Create `tests/voice_flow/test_flow.py`:
```python
from unittest.mock import MagicMock
from voice_flow.flow import make_flow

CFG = {"ollama_url": "http://o/api/generate", "model": "gemma4:12b-it-qat",
       "temperature": 0, "system_prompt": "clean it"}


def test_flow_posts_and_returns_cleaned_text():
    resp = MagicMock(); resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": "Hello, world."}
    poster = MagicMock(return_value=resp)
    fn = make_flow(CFG, poster=poster)
    out = fn("um hello world")
    assert out == "Hello, world."
    body = poster.call_args.kwargs["json"]
    assert body["model"] == "gemma4:12b-it-qat"
    assert body["system"] == "clean it"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert "um hello world" in body["prompt"]


def test_flow_returns_original_on_error():
    poster = MagicMock(side_effect=RuntimeError("ollama down"))
    fn = make_flow(CFG, poster=poster)
    assert fn("raw text") == "raw text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/voice_flow/test_flow.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the flow cleanup**

Create `voice_flow/flow.py`:
```python
"""Local Ollama cleanup/format pass for flow dictation."""
from __future__ import annotations
import logging
import requests

log = logging.getLogger("voice_flow.flow")


def make_flow(cfg_flow: dict, poster=requests.post):
    url = cfg_flow.get("ollama_url", "http://127.0.0.1:11434/api/generate")
    model = cfg_flow.get("model", "gemma4:12b-it-qat")
    system = cfg_flow.get("system_prompt", "")
    temperature = cfg_flow.get("temperature", 0)

    def flow_fn(text: str) -> str:
        try:
            resp = poster(url, json={
                "model": model, "prompt": text, "system": system,
                "stream": False, "options": {"temperature": temperature},
            }, timeout=60)
            resp.raise_for_status()
            return (resp.json().get("response") or text).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("flow cleanup failed (%s); returning raw text", e)
            return text

    return flow_fn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/voice_flow/test_flow.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add voice_flow/flow.py tests/voice_flow/test_flow.py
git commit -m "feat(voice_flow): Ollama flow cleanup pass"
```

---

### Task 8: Agent client (Fluid say → stream → say_aloud)

**Files:**
- Create: `voice_flow/agent_client.py`
- Test: `tests/voice_flow/test_agent_client.py`

**Interfaces:**
- Consumes: `Config.agent` (`fluid_url`, `default_agent`).
- Produces:
  - `voice_flow.agent_client.AgentReply` dataclass: `text: str`, `agent_id: str`.
  - `voice_flow.agent_client.AgentClient(fluid_url, default_agent, session_id="baza-flow", http=requests)` with:
    - `.ask(transcript: str, agent_id: str | None = None) -> AgentReply` — POST `/api/fluid/say` `{session_id, text, agent_id}`, then GET `/api/fluid/stream?session_id=...` (SSE) and concatenate `agent_token` event `data` payloads until a `done`/`end` event or stream close; returns the assembled reply. `agent_id` defaults to `default_agent`.
    - `.speak(text: str, agent_id: str) -> None` — POST `/api/fluid/say_aloud` `{text, agent_id}` (Fluid synthesizes + plays on host speakers).
- SSE parsing: each event is lines `event: <name>` / `data: <json-or-text>`; tokens arrive as `event: agent_token` with `data:` = the text chunk (JSON string or raw). Assemble by stripping the `data: ` prefix and JSON-decoding when it parses.

- [ ] **Step 1: Write the failing test**

Create `tests/voice_flow/test_agent_client.py`:
```python
from unittest.mock import MagicMock
from voice_flow.agent_client import AgentClient, AgentReply

SSE = (
    b'event: agent_token\ndata: "Hello"\n\n'
    b'event: agent_token\ndata: ", boss."\n\n'
    b'event: done\ndata: {}\n\n'
)


def _http():
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=200)
    stream_resp = MagicMock()
    stream_resp.iter_lines.return_value = SSE.split(b"\n")
    http.get.return_value = stream_resp
    return http


def test_ask_posts_say_then_assembles_stream():
    http = _http()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    reply = c.ask("what's the status")
    assert isinstance(reply, AgentReply)
    assert reply.text == "Hello, boss."
    assert reply.agent_id == "specter_voss"
    say_url = http.post.call_args_list[0].args[0]
    assert say_url == "http://fluid/api/fluid/say"
    body = http.post.call_args_list[0].kwargs["json"]
    assert body["text"] == "what's the status"
    assert body["agent_id"] == "specter_voss"


def test_speak_posts_say_aloud():
    http = _http()
    c = AgentClient("http://fluid", "specter_voss", http=http)
    c.speak("done", "nova_sterling")
    called = [call.args[0] for call in http.post.call_args_list]
    assert "http://fluid/api/fluid/say_aloud" in called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/voice_flow/test_agent_client.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the agent client**

Create `voice_flow/agent_client.py`:
```python
"""Headless Fluid client for talk-to-agent + spoken replies."""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
import requests

log = logging.getLogger("voice_flow.agent_client")


@dataclass
class AgentReply:
    text: str
    agent_id: str


class AgentClient:
    def __init__(self, fluid_url: str, default_agent: str,
                 session_id: str = "baza-flow", http=requests):
        self._base = fluid_url.rstrip("/")
        self._default = default_agent
        self._sid = session_id
        self._http = http

    def ask(self, transcript: str, agent_id: str | None = None) -> AgentReply:
        agent = agent_id or self._default
        self._http.post(f"{self._base}/api/fluid/say",
                        json={"session_id": self._sid, "text": transcript, "agent_id": agent},
                        timeout=30)
        resp = self._http.get(f"{self._base}/api/fluid/stream",
                              params={"session_id": self._sid}, stream=True, timeout=120)
        text = self._assemble(resp)
        return AgentReply(text=text, agent_id=agent)

    def _assemble(self, resp) -> str:
        event = None
        parts: list[str] = []
        for raw in resp.iter_lines():
            line = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            if line is None:
                continue
            line = line.strip()
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
                if event in ("done", "end"):
                    break
                if event == "agent_token":
                    try:
                        payload = json.loads(payload)
                    except Exception:  # noqa: BLE001
                        pass
                    parts.append(payload if isinstance(payload, str) else "")
        return "".join(parts).strip()

    def speak(self, text: str, agent_id: str) -> None:
        if not text:
            return
        self._http.post(f"{self._base}/api/fluid/say_aloud",
                        json={"text": text, "agent_id": agent_id}, timeout=60)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/voice_flow/test_agent_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add voice_flow/agent_client.py tests/voice_flow/test_agent_client.py
git commit -m "feat(voice_flow): Fluid agent client (say/stream/say_aloud)"
```

---

### Task 9: Tray indicator + chimes (best-effort)

**Files:**
- Create: `voice_flow/indicator.py`
- Test: `tests/voice_flow/test_indicator.py`

**Interfaces:**
- Consumes: `Config.audio` (`chimes`).
- Produces: `voice_flow.indicator.Indicator(chimes=True, runner=subprocess.run)` with `.set_state(state: str)` (one of `idle/listening/thinking/speaking`, updates tray glyph/color; no-op if tray unavailable) and `.chime(name: str)` (plays a short tone via `aplay` when `chimes` is on; `name` ∈ `start/stop/error`). Must never raise if the tray or audio device is missing — log and continue.

- [ ] **Step 1: Write the failing test**

Create `tests/voice_flow/test_indicator.py`:
```python
from unittest.mock import MagicMock
from voice_flow.indicator import Indicator


def test_set_state_records_current_state():
    ind = Indicator(chimes=False, runner=MagicMock())
    ind.set_state("listening")
    assert ind.state == "listening"


def test_chime_plays_when_enabled():
    run = MagicMock()
    ind = Indicator(chimes=True, runner=run)
    ind.chime("start")
    assert run.called


def test_chime_silent_when_disabled():
    run = MagicMock()
    ind = Indicator(chimes=False, runner=run)
    ind.chime("start")
    run.assert_not_called()


def test_never_raises_on_runner_error():
    run = MagicMock(side_effect=OSError("no audio"))
    ind = Indicator(chimes=True, runner=run)
    ind.chime("start")  # must not raise
    ind.set_state("idle")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/voice_flow/test_indicator.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the indicator**

Create `voice_flow/indicator.py`:
```python
"""Best-effort tray indicator + audio chimes. Never fatal."""
from __future__ import annotations
import logging
import subprocess

log = logging.getLogger("voice_flow.indicator")

_GLYPH = {"idle": "⚪", "listening": "🔴", "thinking": "🟡", "speaking": "🟢"}


class Indicator:
    def __init__(self, chimes: bool = True, runner=subprocess.run):
        self.chimes = chimes
        self._run = runner
        self.state = "idle"
        self._tray = None  # pystray icon; initialized lazily in start()

    def set_state(self, state: str) -> None:
        self.state = state
        try:
            if self._tray is not None:
                self._tray.title = f"Baza Flow {_GLYPH.get(state, '')}"
        except Exception as e:  # noqa: BLE001
            log.debug("tray update failed: %s", e)

    def chime(self, name: str) -> None:
        if not self.chimes:
            return
        # Simple, dependency-free tones via ALSA's speaker-test-like beep.
        freq = {"start": 880, "stop": 660, "error": 220}.get(name, 700)
        try:
            self._run(["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1",
                       _tone_path(freq)], timeout=2)
        except Exception as e:  # noqa: BLE001
            log.debug("chime failed: %s", e)

    def start(self) -> None:
        try:
            import pystray  # noqa: F401
            # Tray is optional polish; if the environment lacks a status area,
            # the daemon still runs headless. Left minimal by design.
        except Exception as e:  # noqa: BLE001
            log.info("tray unavailable, running headless: %s", e)


def _tone_path(freq: int) -> str:
    """Generate a 120ms sine tone WAV once per freq, cached in /tmp."""
    import math, os, struct, wave
    path = f"/tmp/baza-flow-tone-{freq}.wav"
    if os.path.exists(path):
        return path
    sr, dur = 16000, 0.12
    with wave.open(path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        frames = b"".join(
            struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / sr)))
            for i in range(int(sr * dur))
        )
        w.writeframes(frames)
    return path
```
Note: the test passes a WAV path via `_tone_path`; the raw-format `aplay` args are replaced by a plain `["aplay","-q",path]` call. Use this simpler `chime` body instead:
```python
    def chime(self, name: str) -> None:
        if not self.chimes:
            return
        freq = {"start": 880, "stop": 660, "error": 220}.get(name, 700)
        try:
            self._run(["aplay", "-q", _tone_path(freq)], timeout=2)
        except Exception as e:  # noqa: BLE001
            log.debug("chime failed: %s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/voice_flow/test_indicator.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add voice_flow/indicator.py tests/voice_flow/test_indicator.py
git commit -m "feat(voice_flow): tray indicator + audio chimes (best-effort)"
```

---

### Task 10: Wire `main()`, integration smoke, systemd unit, and runbook

**Files:**
- Modify: `voice_flow/daemon.py` (add `main()` + `build_daemon()`)
- Create: `voice_flow/README.md`
- Create: `tests/voice_flow/test_integration_smoke.py`
- Create (on host, documented in README): `~/.config/systemd/user/baza-flow.service`

**Interfaces:**
- Produces: `voice_flow.daemon.build_daemon(config) -> Daemon` (constructs real Transcriber/Injector/flow_fn/AgentClient/Indicator from config) and `voice_flow.daemon.main()` (loads config, builds daemon, starts HotkeyListener + Indicator, blocks). `build_daemon` must be importable/testable without starting hotkeys.

- [ ] **Step 1: Write the failing integration smoke test**

Create `tests/voice_flow/test_integration_smoke.py`:
```python
import numpy as np
from unittest.mock import MagicMock, patch
from voice_flow.config import load_config
from voice_flow.recorder import frames_to_wav
from voice_flow.daemon import Daemon, build_daemon


def test_build_daemon_returns_daemon():
    cfg = load_config()
    with patch("voice_flow.daemon.Transcriber"), \
         patch("voice_flow.daemon.AgentClient"):
        d = build_daemon(cfg)
    assert isinstance(d, Daemon)


def test_end_to_end_raw_injection_with_fixture_wav(tmp_path):
    wav = frames_to_wav((np.random.randn(16000) * 0.01).astype("float32"),
                        16000, str(tmp_path / "u.wav"))
    cfg = load_config()
    tr = MagicMock(); tr.transcribe.return_value = "book the crew for tuesday"
    inj = MagicMock(); inj.inject.return_value = 25
    d = Daemon(config=cfg, transcriber=tr, injector=inj,
               recorder_factory=MagicMock(), commands_enabled=True)
    out = d.handle_utterance("raw", wav)
    assert out == "book the crew for tuesday"
    inj.inject.assert_called_once_with("book the crew for tuesday")


def test_end_to_end_agent_mode_speaks(tmp_path):
    cfg = load_config()
    tr = MagicMock(); tr.transcribe.return_value = "specter what is our cash position"
    inj = MagicMock()
    ac = MagicMock()
    from voice_flow.agent_client import AgentReply
    ac.ask.return_value = AgentReply(text="Cash is healthy.", agent_id="specter_voss")
    d = Daemon(config=cfg, transcriber=tr, injector=inj, recorder_factory=MagicMock(),
               agent_client=ac, commands_enabled=True)
    out = d.handle_utterance("agent", str(tmp_path / "x.wav"))
    ac.ask.assert_called_once()
    ac.speak.assert_called_once_with("Cash is healthy.", "specter_voss")
    assert out == "Cash is healthy."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/voice_flow/test_integration_smoke.py -v`
Expected: FAIL — `build_daemon` not defined.

- [ ] **Step 3: Add `build_daemon()` and `main()` to the daemon**

Append to `voice_flow/daemon.py`:
```python
from voice_flow.stt import Transcriber
from voice_flow.inject import Injector
from voice_flow.flow import make_flow
from voice_flow.agent_client import AgentClient
from voice_flow.indicator import Indicator
from voice_flow.recorder import Recorder
from voice_flow.hotkeys import HotkeyListener
from voice_flow.config import load_config


def build_daemon(config):
    stt = config.stt
    transcriber = Transcriber(
        model=stt.get("model", "base"),
        compute_type=stt.get("compute_type", "int8"),
        device=stt.get("device", "cpu"),
        fallback_url=stt.get("fluid_stt_fallback"),
    )
    injector = Injector(method=config.injection.get("method", "paste"))
    flow_fn = make_flow(config.flow)
    agent_client = AgentClient(
        fluid_url=config.agent.get("fluid_url", "http://127.0.0.1:8889"),
        default_agent=config.agent.get("default_agent", "specter_voss"),
    )
    indicator = Indicator(chimes=config.audio.get("chimes", True))
    sr = config.audio.get("samplerate", 16000)
    dev = config.audio.get("input_device")
    return Daemon(
        config=config, transcriber=transcriber, injector=injector,
        recorder_factory=lambda: Recorder(device=dev, samplerate=sr),
        flow_fn=flow_fn, agent_client=agent_client,
        commands_enabled=config.commands.get("enabled", True), indicator=indicator,
    )


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load_config()
    daemon = build_daemon(config)
    daemon.indicator.start()
    bindings = {
        config.hotkeys.get("raw", "ctrl+space"): "raw",
        config.hotkeys.get("flow", "ctrl+shift+space"): "flow",
        config.hotkeys.get("agent", "ctrl+alt+space"): "agent",
        config.hotkeys.get("cancel", "esc"): "cancel",
    }
    listener = HotkeyListener(bindings, on_press=daemon.on_press, on_release=daemon.on_release)
    listener.start()
    log.info("Baza Flow ready. Hotkeys: %s", bindings)
    import signal
    signal.pause()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/voice_flow/test_integration_smoke.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full suite**

Run: `venv/bin/pytest tests/voice_flow/ -v`
Expected: PASS (all tests across tasks 1–10 green).

- [ ] **Step 6: Write the runbook + systemd unit**

Create `voice_flow/README.md`:
```markdown
# Baza Flow — local voice dictation daemon

Handy-style push-to-talk dictation + two-way agent voice comms, fully local.

## Install / run
    cd ~/baza-empire/agent-framework-v3
    venv/bin/pip install sounddevice pynput pystray watchdog
    venv/bin/python -m voice_flow.daemon        # foreground test run

## Hotkeys (config.yaml)
- Ctrl+Space        raw dictation → types into focused window
- Ctrl+Shift+Space  flow dictation → Ollama-cleaned text
- Ctrl+Alt+Space    talk to an agent → spoken reply via Fluid
- Esc               cancel current capture

## systemd (user service)
Create `~/.config/systemd/user/baza-flow.service`:

    [Unit]
    Description=Baza Flow voice dictation daemon
    After=graphical-session.target

    [Service]
    ExecStart=%h/baza-empire/agent-framework-v3/venv/bin/python -m voice_flow.daemon
    WorkingDirectory=%h/baza-empire/agent-framework-v3
    Environment=DISPLAY=:0
    Restart=on-failure

    [Install]
    WantedBy=default.target

Then:
    systemctl --user daemon-reload
    systemctl --user enable --now baza-flow
    journalctl --user -u baza-flow -f

## Coexistence with Handy (Serge-confirmed flip)
Baza Flow's default hotkeys collide with Handy's. To make Baza Flow primary,
disable Handy's autostart (Handy stays installed as a fallback):
    mv ~/.config/autostart/Handy.desktop ~/.config/autostart/Handy.desktop.disabled
Re-enable Handy by moving it back.

## Dependencies
- Requires the Fluid dev server (`systemctl --user status fluid-dev-server`, :8889)
  for talk-to-agent mode. Raw/flow dictation work without it.
- Requires Ollama (`gemma4:12b-it-qat`) for flow dictation.
- X11 only (xdotool/xclip). DISPLAY must be set for the service.
```

- [ ] **Step 7: Commit**

```bash
git add voice_flow/daemon.py voice_flow/README.md tests/voice_flow/test_integration_smoke.py
git commit -m "feat(voice_flow): main() + build_daemon, integration smoke, runbook"
```

- [ ] **Step 8: Manual verification (host, after install)**

Run the daemon in the foreground and verify each mode by hand:
```bash
cd /home/switchhacker/baza-empire/agent-framework-v3
venv/bin/python -m voice_flow.daemon
```
- Focus a text editor, hold `Ctrl+Space`, say a sentence, release → text appears.
- Hold `Ctrl+Shift+Space`, say a messy sentence → cleaned text appears (needs Ollama up).
- Hold `Ctrl+Alt+Space`, say "Specter, what's our status" → spoken reply (needs Fluid up).
- Say "new line" in raw mode → a newline is inserted, not the words.

---

## Self-Review

**1. Spec coverage:**
- §3 modules → Tasks 1–10 create every listed module (config, recorder, stt, inject, commands, flow, agent_client, hotkeys, indicator, daemon). ✓
- §4 four modes → raw (T5), flow (T7), agent (T8), cancel (T5 `on_press`/`_abort`), voice commands (T6). ✓
- §5 engine reuse → in-process whisper + Fluid fallback (T3), Ollama flow (T7), Fluid say/stream/say_aloud (T8). ✓
- §6 injection clipboard-paste save/restore (T4). ✓
- §7 config.yaml (T1). ✓
- §8 tray + chimes (T9). ✓
- §9 Handy coexistence flip (T10 README). ✓
- §10 testing per module + integration smoke (every task + T10). ✓
- §11 systemd unit (T10 README). ✓
- §12 build order → task order matches (config→recorder/stt→raw→commands→flow→agent→indicator→wire). ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code and exact commands. The one narrative note in T9 explicitly replaces the raw-format `chime` body with the simpler `aplay path` version the test asserts. ✓

**3. Type consistency:**
- `Config` sections used as dicts consistently (`cfg.agent.get(...)`, `config.stt.get(...)`). ✓
- `Transcriber.transcribe(wav_path)`, `Injector.inject/press/delete_last`, `match_command(text, agents) -> Command|None`, `make_flow(cfg_flow) -> flow_fn`, `AgentClient.ask -> AgentReply` / `.speak`, `Recorder.start/stop/abort`, `Indicator.set_state/chime/start` — names match between definition tasks and daemon usage (T5/T6/T10). ✓
- `Daemon.handle_utterance(mode, wav_path) -> str` and `_run_command(cmd)` consistent across T5/T6/T10 tests. ✓
- Note: T5 defines `handle_utterance` with a raw/flow/agent branch, T6 *replaces* that branch to add command interception and the `active_dictation_mode` indirection — the plan states the replacement explicitly so there is one final return path. ✓
