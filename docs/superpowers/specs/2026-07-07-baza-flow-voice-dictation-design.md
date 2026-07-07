# Baza Flow — voice dictation + agent voice comms daemon

**Date:** 2026-07-07
**Status:** Design approved (brainstorming) — pending spec review
**Working name:** Baza Flow (`baza-flow`) — renameable

## 1. Summary

A host-level Python daemon that turns speech into text in **any focused window** (Claude CLI, browser, editor, dashboard) and optionally into a **two-way spoken conversation** with the Baza agents. It is a purpose-built replacement for Handy ("Handy but ours") with a Wispr-Flow-style local cleanup pass added, and it is **fully local** (no cloud APIs) per the empire's hard local-first rule.

Primary job (as chosen in brainstorming): **universal dictation**. Agent voice comms and flow cleanup are first-class secondary capabilities, all shipping in v1.

### Why build instead of keep Handy
Handy (cjpais, `/usr/bin/handy`, Rust/Tauri) is a closed host app outside the repos. Serge wants an owned tool that (a) integrates natively with Baza/Fluid/agents, (b) does local LLM "flow" cleanup instead of cloud post-processing, and (c) is maintainable inside the all-Python codebase. Everything the daemon needs already runs on baza in the shared venv — **faster-whisper** (STT), **edge-tts** (TTS via Fluid), **Ollama** (`gemma4:12b-it-qat` for flow cleanup) — so this is integration + a thin new daemon, not new heavy infrastructure. The session is X11, so `xdotool` injection works exactly as Handy's does today.

## 2. Goals / Non-goals

**Goals (v1)**
- Global-hotkey push-to-talk dictation into the focused window, instant (no LLM in the hot path).
- A separate "flow" hotkey that runs an Ollama cleanup/format pass before typing.
- A "talk-to-agent" hotkey: speak → routed to a Baza agent → reply spoken aloud (edge-tts) and optionally typed.
- Spoken meta-commands ("new line", "scratch that", "send to Specter", "switch to flow") intercepted instead of typed.
- Tray indicator + audio chimes for state feedback.
- YAML config (hotkeys, models, prompts, default agent, mic device, injection method), hot-reloaded.
- Runs as a systemd **user** service, autostarts on login.
- Fully local; no cloud calls anywhere in any mode.

**Non-goals (v1 — hooks left clean, additive later)**
- Real-time streaming *dictation* (type-as-you-speak). Dictation waits for the full utterance, then injects cleanly.
- Raw-audio wake word ("hey baza" with no keypress).
- Wayland support (X11 only, matching current session).
- Edge / ESP32-S3 mic as an input source.
- A settings GUI (config is YAML + tray on/off toggles only).

## 3. Architecture

### Placement & runtime
- New package: `baza-empire/agent-framework-v3/voice_flow/`.
- Runs from the **shared venv** (the vision worktree symlinks the same venv), so faster-whisper / edge-tts / ollama client are already present.
- Single long-lived process managed by a systemd **user** unit `baza-flow.service` (sibling model to `fluid-dev-server.service`), enabled via `default.target.wants`, autostarts on login.
- X11 → `xdotool` for injection; clipboard access via `xclip`/`xsel` (with save/restore).

### Modules (each a small, independently testable unit)

| Module | Responsibility | Depends on |
|---|---|---|
| `daemon.py` | Lifecycle, wires modules, owns the mode state machine | all below |
| `config.py` | Load/validate `config.yaml`, hot-reload on file change | PyYAML |
| `hotkeys.py` | Global hotkey listener; maps keychord → mode/action; press-and-hold + tap semantics | `pynput` (primary) / `evdev` fallback |
| `recorder.py` | Mic capture (press-to-talk), 16 kHz mono WAV, start/stop/abort | `sounddevice`, `soundfile` |
| `stt.py` | faster-whisper `base` int8 **in-process**; WAV → transcript; HTTP fallback to Fluid `/api/fluid/stt` | `faster-whisper` |
| `flow.py` | Ollama cleanup/format pass; prompt-driven; temp 0 | Ollama HTTP `:11434` |
| `commands.py` | Match spoken meta-commands against a small grammar; return an action or `None` (dictate normally) | — |
| `inject.py` | Put text into the focused window: clipboard-paste (save/restore clipboard, `Ctrl+V`) or type; per-config | `xdotool`, `xclip` |
| `agent_client.py` | Headless Fluid client: POST transcript to `/api/fluid/say`, consume SSE, play streamed TTS on host speakers | `requests`/`httpx`, `aplay`/`ffplay` |
| `indicator.py` | Tray icon + state, start/stop chimes | `pystray`/AppIndicator; chimes via `aplay` |

### Mode state machine (in `daemon.py`)
States: `IDLE → LISTENING → (TRANSCRIBING) → [RAW: INJECT | FLOW: FLOWING→INJECT | AGENT: SENDING→SPEAKING(+optional INJECT)] → IDLE`. `Esc` while `LISTENING`/`TRANSCRIBING` aborts to `IDLE`. Indicator reflects each state.

## 4. The four modes

| Mode | Default hotkey | Pipeline |
|---|---|---|
| **Raw dictation** | `Ctrl+Space` | record → stt → commands filter → inject (verbatim). No LLM. |
| **Flow dictation** | `Ctrl+Shift+Space` | record → stt → commands filter → flow (Ollama polish) → inject. |
| **Talk-to-agent** | `Ctrl+Alt+Space` | record → stt → agent_client (Fluid routes by name / default agent) → reply spoken via edge-tts + optionally injected. |
| **Cancel** | `Esc` (while recording) | abort current capture, back to IDLE. |

Hotkeys are press-and-hold to record, release to finalize (Handy-style), configurable to toggle mode in YAML.

### Voice commands (`commands.py`)
While in any dictation mode, the transcript is first checked against a small grammar before injection. Matches execute an action instead of being typed; non-matches dictate normally.

Initial grammar (extensible, config-listed):
- Editing: `new line` / `new paragraph` → xdotool Return(s); `scratch that` → delete last injected span; `select all` → `Ctrl+A`; `undo that` → `Ctrl+Z`.
- Routing/control: `send to <agent>` → switch this utterance to talk-to-agent targeting `<agent>`; `switch to flow` / `switch to raw` → change active dictation mode; `stop listening` → abort.

Matching is anchored/normalized (lowercased, punctuation-stripped, leading/trailing only) to avoid eating normal dictation. Ambiguity resolves to "dictate normally" (safe default).

## 5. Engine reuse (all local, no duplication)

- **STT**: faster-whisper `base` int8 loaded in-process for lowest dictation latency (same model class Fluid uses). If the model fails to load, fall back to POSTing the WAV to Fluid's `/api/fluid/stt`.
- **Flow cleanup**: Ollama `POST /api/generate`, model `gemma4:12b-it-qat`, `temperature: 0`, tight system prompt: *"Rewrite the dictated text with correct punctuation, capitalization, and paragraphing. Remove filler words. Preserve meaning and wording. Output only the cleaned text."* Prompt lives in config (editable).
- **Talk-to-agent + TTS**: **via Fluid** (chosen). The daemon POSTs the transcript to the Fluid server (`:8889`, vision worktree), which already does agent addressing ("Specter, …" via `address_parser.py`), per-agent voices (`voices.yaml`), LLM streaming, and sentence-chunked TTS. The daemon consumes the SSE stream and plays streamed audio chunks on host speakers (`aplay`/`ffplay`), mirroring Fluid's own `/say_aloud`. If Fluid is unreachable, the mode surfaces an audible error chime + tray error state (does not crash the daemon).

## 6. Injection (`inject.py`)

**Default: clipboard-paste with save/restore** (chosen).
1. Save current clipboard (`xclip -o`), 2. set clipboard to text (`xclip -i`), 3. `xdotool key ctrl+v`, 4. restore prior clipboard after a short delay.
- Handles long/multiline/unicode/emoji cleanly.
- Config option `injection: paste | type` to fall back to Handy-style char typing for terminals that block paste.
- `scratch that` uses the length of the last injected span to delete it (tracked in `daemon.py`).

## 7. Config — `voice_flow/config.yaml`

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
  default_agent: "specter"
  speak_reply: true
  type_reply: false
audio:
  input_device: null       # null = system default
  chimes: true
injection:
  method: "paste"          # paste | type
commands:
  enabled: true
```
Config is validated on load and **hot-reloaded** on file change (watchdog or mtime poll).

## 8. Feedback (`indicator.py`)

- Tray icon with four states: idle / listening / thinking / speaking (color or glyph).
- Short chimes on record-start and record-stop (distinct), and an error chime on failure.
- All feedback individually toggleable in config; daemon runs headless if the tray can't initialize (logs a warning, does not crash).

## 9. Coexistence with Handy

The default hotkeys collide with Handy's (`ctrl+space`, `ctrl+shift+space`). Install step **disables Handy's autostart** (`~/.config/autostart/Handy.desktop`) so the two don't both grab the hotkey — but this is a **Serge-confirmed flip**, and Handy stays installed as a fallback. Documented in the runbook; not done silently.

## 10. Testing

- **Unit** (per module, mocked I/O):
  - `stt.py`: fixture WAV → asserted transcript (tiny known clip).
  - `flow.py`: mocked Ollama response → asserts prompt shape + returns cleaned text; golden-prompt test.
  - `commands.py`: table-driven grammar tests (each command phrase → expected action; near-misses → dictate-normally).
  - `inject.py`: dry-run capturing the exact `xdotool`/`xclip` argv (no real X calls); clipboard save/restore ordering.
  - `agent_client.py`: mocked Fluid SSE stream → asserts POST payload + that audio chunks are dispatched to the player; Fluid-down path → error state, no crash.
  - `config.py`: valid/invalid YAML, hot-reload picks up changes.
- **Integration smoke**: drive `daemon.py` end-to-end with a fixture WAV injected at the recorder seam, mode = raw/flow/agent, assert the right sink received the right text (injection argv / agent POST).
- Tests live in `tests/voice_flow/`, run under the shared venv.

## 11. Systemd unit (runbook)

`~/.config/systemd/user/baza-flow.service`:
```ini
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
```
Manage: `systemctl --user restart baza-flow`. Requires `DISPLAY` for xdotool; verify `$XAUTHORITY` if injection fails.

## 12. Build order (informs the implementation plan)

1. `config.py` + package skeleton + systemd unit (no behavior).
2. `recorder.py` + `stt.py` → **raw dictation** end-to-end via `inject.py` (paste). *First usable milestone.*
3. `commands.py` grammar + wire into dictation.
4. `flow.py` → **flow dictation**.
5. `agent_client.py` → **talk-to-agent** via Fluid.
6. `indicator.py` tray + chimes.
7. Coexistence flip + runbook + smoke test.

## 13. Open items / risks

- **pynput global hotkeys under X11**: reliable, but modifier chords + hold semantics need a real-hardware check; `evdev` is the fallback (needs input-group perms).
- **faster-whisper first-load latency**: model loads at daemon start (kept warm), not per-utterance — verify RAM headroom alongside Fluid's own whisper instance.
- **Fluid dependency for agent mode**: talk-to-agent needs `fluid-dev-server` running; acceptable (it autostarts), surfaced as an error state when down.
- **Clipboard restore race**: restore after a short fixed delay; if paste is slow the restore could clobber — tune delay, make configurable.
