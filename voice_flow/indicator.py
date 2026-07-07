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
        freq = {"start": 880, "stop": 660, "error": 220}.get(name, 700)
        try:
            self._run(["aplay", "-q", _tone_path(freq)], timeout=2)
        except Exception as e:  # noqa: BLE001
            log.debug("chime failed: %s", e)

    def start(self) -> None:
        """Best-effort tray icon. Any failure logs and runs headless."""
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse((6, 6, 26, 26), fill=(80, 170, 255, 255))
            icon = pystray.Icon("baza-flow", img,
                                title=f"Baza Flow {_GLYPH['idle']}")
            icon.run_detached()
            self._tray = icon
        except Exception as e:  # noqa: BLE001 — tray is optional polish
            log.info("tray unavailable, running headless: %s", e)
            self._tray = None


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
