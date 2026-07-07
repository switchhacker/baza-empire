"""Spoken meta-command grammar."""
from __future__ import annotations
import re
from dataclasses import dataclass

AGENT_NAMES = ["specter", "simon", "claw", "phil", "sam", "rex", "duke", "scout", "nova"]

# Spoken short names → full Fluid agent ids (config/agents.yaml roster).
AGENT_ID_BY_NAME = {
    "specter": "specter_voss",
    "simon": "simon_bately",
    "claw": "claw_batto",
    "phil": "phil_hass",
    "sam": "sam_axe",
    "rex": "rex_valor",
    "duke": "duke_harmon",
    "scout": "scout_reeves",
    "nova": "nova_sterling",
}


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
