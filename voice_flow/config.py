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
