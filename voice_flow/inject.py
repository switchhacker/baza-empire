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
