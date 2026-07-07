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
