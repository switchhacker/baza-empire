"""Inject text into the focused X11 window via xdotool/xclip."""
from __future__ import annotations
import re
import subprocess
import time

_CLIP = ["xclip", "-selection", "clipboard"]

# WM_CLASS names (lowercased) where paste is ctrl+shift+v, not ctrl+v.
_TERMINAL_CLASSES = {
    "gnome-terminal", "gnome-terminal-server", "kgx", "konsole", "xterm",
    "uxterm", "urxvt", "rxvt", "alacritty", "kitty", "terminator", "tilix",
    "xfce4-terminal", "mate-terminal", "lxterminal", "st", "st-256color",
    "wezterm", "org.wezfurlong.wezterm", "sakura", "guake", "terminology",
}


class Injector:
    def __init__(self, method: str = "paste", runner=subprocess.run, restore_delay: float = 0.5):
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

    def _focused_window_classes(self) -> set:
        # xdotool on this box lacks getwindowclassname; xprop WM_CLASS instead.
        try:
            r = self._run(["xdotool", "getactivewindow"], capture_output=True)
            win = (getattr(r, "stdout", b"") or b"").decode(errors="replace").strip()
            if not win:
                return set()
            r = self._run(["xprop", "-id", win, "WM_CLASS"], capture_output=True)
            out = (getattr(r, "stdout", b"") or b"").decode(errors="replace")
            return {m.lower() for m in re.findall(r'"([^"]*)"', out)}
        except Exception:  # noqa: BLE001
            return set()

    def _paste(self, text: str) -> None:
        prev = None
        try:
            r = self._run(_CLIP + ["-o"], capture_output=True)
            prev = getattr(r, "stdout", None)
        except Exception:  # noqa: BLE001
            prev = None
        # Terminals paste with ctrl+shift+v; ctrl+v there is a dead key
        # (readline quoted-insert) and the transcript silently vanishes.
        chord = ("ctrl+shift+v" if self._focused_window_classes() & _TERMINAL_CLASSES
                 else "ctrl+v")
        self._run(_CLIP + ["-i"], input=text.encode())
        self._run(["xdotool", "key", chord])
        if prev is not None:
            time.sleep(self._restore_delay)
            self._run(_CLIP + ["-i"], input=prev if isinstance(prev, bytes) else str(prev).encode())

    def press(self, keys: str) -> None:
        self._run(["xdotool", "key", keys])

    def delete_last(self, n: int) -> None:
        if n > 0:
            self._run(["xdotool", "key", "--repeat", str(n), "BackSpace"])
