# Baza Flow — local voice dictation daemon

Handy-style push-to-talk dictation + two-way agent voice comms, fully local.

## Install / run
    cd ~/baza-empire/agent-framework-v3
    sudo apt install -y libportaudio2   # PortAudio system lib for mic capture
    venv/bin/pip install sounddevice pynput pystray watchdog
    venv/bin/python -m voice_flow.daemon        # foreground test run

Pinned versions for all voice_flow deps live in `requirements.txt`
(faster-whisper, sounddevice, soundfile, pynput, pystray, watchdog, numpy).

## Config hot-reload
`config.yaml` is polled every 2s, but only settings read fresh on each
utterance actually hot-reload.

- **Live (no restart):** `agent.speak_reply`, `agent.type_reply`.
- **Restart required:** hotkeys, STT model, flow model/prompt/URL,
  injection method, `commands.enabled`, agent `fluid_url`/`default_agent`
  — all baked in at daemon construction
  (`systemctl --user restart baza-flow`).

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
