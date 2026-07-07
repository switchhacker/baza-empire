# Baza Flow — Post-Review Fix Plan

> Executes the MUST-FIX findings from the whole-branch review of the Baza Flow daemon (`voice_flow/`). Serge chose "fix everything now." Grounded in the VERIFIED live Fluid contract (not assumptions).

**Base after Task 10:** commit `fe9bc53`. Full suite 31/31 green before fixes.

## Verified Fluid contract (source of truth for Wave A)

From the live vision worktree (`agent-framework-v3-vision/`):
- `POST /api/fluid/say` `{session_id, text, agent_id}` **requires a pre-existing DB session** → `404 {"error":"session not found"}` otherwise. Returns `200 {"ok":true,"target_agent":...}` immediately; the turn runs in a background thread, reply comes over SSE.
- **No JSON endpoint mints a session.** The only HTTP way is `GET /fluid` (HTML page) which calls `create_session()` and renders `session_id` into `fluid.html`. Client must GET that page and parse the id out of the HTML.
- `GET /api/fluid/stream?session_id=<sid>` (SSE). Each frame: `id: N\nevent: <type>\ndata: <json.dumps(payload)>\n\n`. **`data:` is always a JSON object.**
- Reply tokens: `event: agent_token`, `data:` = `{"agent_id":..,"sentence_id":..,"ordinal":..,"text":"<one sentence>","spoken":true}`. Reply text is in `.text`, **one sentence per event**.
- Turn terminator: `event: agent_turn_end`, `data:` = `{"agent_id":..}`. The stream is an **infinite** generator; on idle it emits a comment keep-alive line `: keep-alive\n\n` every **15s**. The client MUST enforce its own wall-clock deadline and stop at `agent_turn_end`.
- `POST /api/fluid/say_aloud` `{text, agent_id}` — **no session needed**; synthesizes then fire-and-forget plays on host speakers; returns `200 {"ok":true,"via":..}`. Voices arbitrary text; does NOT run the LLM. (Our current `speak()` is already correct.)
- Default `agent_id` across Fluid is `specter_voss`.

## Agent id mapping (source of truth for Wave B name-drift)

`commands.AGENT_NAMES` are short first names for spoken matching; Fluid needs full ids:
`specter→specter_voss, simon→simon_bately, claw→claw_batto, phil→phil_hass, sam→sam_axe, rex→rex_valor, duke→duke_harmon, scout→scout_reeves, nova→nova_sterling`.

## Dependency versions (source of truth for Wave B requirements.txt)

`faster-whisper==1.2.1`, `sounddevice==0.5.5`, `soundfile==0.12.1`, `pynput==1.8.2`, `pystray==0.19.5`, `watchdog==6.0.0`, `numpy==2.4.3`. (Host system lib `libportaudio2` already installed 2026-07-07.)

---

## Wave A — Rework `agent_client.py` against the real Fluid contract (fixes C2)

**Files:** Modify `voice_flow/agent_client.py`; rewrite `tests/voice_flow/test_agent_client.py`.

**New behavior for `AgentClient`:**
- Add `_ensure_session() -> str`: if `self._session` is None, GET `{base}/fluid`, parse the session id from the HTML, cache and return it. Parsing: read the real `agent-framework-v3-vision/dashboard/templates/fluid.html` to see exactly how `session_id` is embedded (e.g. a `const SESSION_ID = "..."`, a `data-session` attribute, or a hidden input), then use a regex that matches that pattern. If parsing fails, raise a clear `RuntimeError("could not obtain Fluid session")`.
- `ask(transcript, agent_id=None) -> AgentReply`:
  - `agent = agent_id or self._default`
  - `sid = self._ensure_session()`
  - POST `{base}/api/fluid/say` `{session_id: sid, text: transcript, agent_id: agent}`. If status is 404, drop the cached session, re-mint once, and retry the POST. If still not 200, raise.
  - Open `GET {base}/api/fluid/stream` `params={session_id: sid}`, `stream=True`, with a connect+read timeout. Iterate lines with an **overall wall-clock deadline** (`deadline_s`, default 60): for each line, skip blank and comment (`:`-prefixed keep-alive) lines; track `event:`; on `event: agent_token` `json.loads(data)` and append `payload.get("text","")`; on `event: agent_turn_end` break. If the deadline passes, break (return what was collected). Join collected sentences with a single space; `.strip()`.
  - return `AgentReply(text=..., agent_id=agent)`
  - Note ordering: open the stream BEFORE posting `say` OR pass `last_event_id=0` when opening after — simplest robust choice: open the stream first, then POST say, then read. (The ring buffer replays from `last_event_id`, but opening-first avoids a race.)
- `speak(text, agent_id)`: unchanged (POST say_aloud, no-op empty).
- Constructor gains `deadline_s: float = 60.0` and `session_id: str | None = None` (existing default agent/http kwargs stay). `self._session` starts as the passed `session_id` (may be None).

**Test (`test_agent_client.py`) — real frame shapes, injected fake `http`:**
- Fake `http.get` for `/fluid` returns an object whose `.text` is HTML containing the session id in the real embedded form (mirror whatever the template uses); assert `_ensure_session` extracts it.
- Fake `http.get` for `/stream` returns an object whose `.iter_lines()` yields REAL-shaped bytes:
  `event: agent_token` / `data: {"agent_id":"specter_voss","text":"Hello Serge.","spoken":true}` / blank; then a `: keep-alive` line; then `event: agent_token` / `data: {"agent_id":"specter_voss","text":"How can I help?","spoken":true}` / blank; then `event: agent_turn_end` / `data: {"agent_id":"specter_voss"}` / blank.
  Assert `ask(...).text == "Hello Serge. How can I help?"` and `agent_id == "specter_voss"`, and that `say` was POSTed with the minted `session_id`.
- Test the 404-then-remint path: first `http.post` to `/say` returns status 404, second returns 200; assert session re-minted (two `/fluid` GETs) and the reply still assembles.
- Test the deadline: an `iter_lines()` that never yields `agent_turn_end` (only keep-alives) must return within the deadline (use a tiny `deadline_s` and a fake clock or a finite keep-alive iterator) — assert `ask` returns (does not hang) with whatever text was collected.
- `speak` posts say_aloud — keep the existing assertion.

**Commit:** `fix(voice_flow): rework Fluid agent client to real say/stream/session contract`

---

## Wave B — Daemon integration, feedback, hot-reload, deps (fixes C3, I1, I2, I3, I4)

**Files:** Modify `voice_flow/daemon.py`, `voice_flow/commands.py`, `voice_flow/indicator.py`, `requirements.txt`, `voice_flow/README.md`; add tests to `tests/voice_flow/test_daemon_commands.py` (or a new `test_daemon_integration.py`).

**B1 — deps + runbook (I4, C1 doc):**
- Append to `requirements.txt` the 7 pinned lines above.
- In `voice_flow/README.md` install section, add before the pip line: `sudo apt install -y libportaudio2   # PortAudio system lib for mic capture`.

**B2 — agent-id mapping (I4 drift, supports I3):**
- In `commands.py`, add `AGENT_ID_BY_NAME = {"specter":"specter_voss","simon":"simon_bately","claw":"claw_batto","phil":"phil_hass","sam":"sam_axe","rex":"rex_valor","duke":"duke_harmon","scout":"scout_reeves","nova":"nova_sterling"}`.
- Test: every entry in `AGENT_NAMES` has a mapping; `match_command("send to nova", AGENT_NAMES)` still returns `Command("route","nova")`.

**B3 — implement "send to <agent>" (I3):**
- `Daemon.__init__`: initialize `self._pending_agent = None`.
- `_do_agent(text, agent_id=None)`: pass `agent_id` through to `self.agent_client.ask(text, agent_id=agent_id)`.
- In `handle_utterance`, after the command-interception block: if `self._pending_agent` is set AND the utterance was not itself a command, resolve `full = AGENT_ID_BY_NAME.get(self._pending_agent, self._pending_agent)`, clear `self._pending_agent = None`, and `return self._do_agent(text, agent_id=full)` (speaking the reply per `speak_reply`). This makes "send to Specter" (utterance 1) target the NEXT utterance (utterance 2) to Specter.
- Tests: (1) route then next utterance → `agent_client.ask` called with `agent_id="specter_voss"` and `_pending_agent` cleared; (2) a normal utterance with no pending route still dictates.

**B4 — thread-safety + never kill the listener (C3):**
- Change `Daemon.on_release` so the transcribe→handle pipeline runs OFF the pynput listener thread: submit `self._process(mode, wav)` to a single-worker `concurrent.futures.ThreadPoolExecutor(max_workers=1)` created in `__init__`. `on_release` returns immediately after `recorder.stop()` + submit.
- `_process(mode, wav)` wraps `handle_utterance` in `try/except Exception`: on error, log, `indicator.chime("error")`, set state error→idle; never propagate.
- Guard overlap: if a job is already running, ignore a new press-start (or drop the new capture) so utterances don't interleave — a simple `self._busy` flag checked in `on_press`.
- Test: an `agent_client.ask` that raises → `handle_utterance` via `_process` does not raise out; `indicator.chime` called with `"error"`; daemon still usable. (Call `_process` directly in the test to avoid real threads, or `.result()` the future.)

**B5 — wire chimes + tray (I2):**
- In `on_press` after `recorder.start()`: `self.indicator.chime("start")` (guard `indicator is not None`).
- In `on_release` after `recorder.stop()`: `self.indicator.chime("stop")`.
- Error chime already added in B4.
- `Indicator.start()`: attempt to actually create a `pystray.Icon` with a small generated PIL image and `icon.run_detached()`, wrapped so any failure logs and runs headless (never fatal). If `pystray`/`PIL` import or backend init fails, degrade silently. Keep it best-effort; chimes are the primary feedback.
- Test: `on_press`/`on_release` with a mock indicator assert `chime("start")`/`chime("stop")` fire; `Indicator.start()` never raises even if pystray unavailable (mock the import to raise).

**B6 — hot-reload poll (I1):**
- In `main()`, after building the daemon, start a `threading.Thread(daemon=True)` that loops: `time.sleep(2); config.reload_if_changed()`. Because `speak_reply`/`type_reply`/`injection method`-style values are read from `self.cfg` at call time, live edits to those take effect without restart. Hotkeys/model changes still need a restart — document this limitation in the README ("hot-reload applies to per-utterance settings; hotkey/model changes need a service restart").
- No unit test required for the thread; add a one-line test that `config.reload_if_changed()` is importable/callable from `main`'s module scope (already covered by test_config). Keep the thread minimal and daemonized.

**Commit:** `fix(voice_flow): thread-safe utterance handling, send-to-agent routing, chimes/tray, hot-reload, deps`

---

## After both waves
- Run full suite `venv/bin/pytest tests/voice_flow/ -v` — everything green (prior 31 + new/changed tests).
- The remaining DEFER minors (config YAML fail-loud, recorder tempdir, hotkeys `_release` extra-key edge, E402) stay as documented follow-ups.
- Live-hardware verification (mic capture, xdotool typing, real hotkeys, real Fluid turn) remains a Serge manual step per README.
