"""
Baza Empire — Intent Router (sub-project #2)

Parses directive phrases like:
    /create new baza project foo type=web-app
    /create new ahb project name="Kitchen Reno" from=chat-123
    /develop <id> Add a contact form
    /render <id>
    /preview <id>
    /test <id>
    /debug <id>
    /deploy <id> [target=local]
    /iterate <id> Refactor the auth flow
    /flash <id> [device=esp32]

Returns a structured envelope:
    {"intent": "<name>", "args": {...}, "errors": [...], "raw": "<original>"}

Loose recognition (case-insensitive, leading slash optional, "create new" or
"new" both work) but a strict args schema downstream code can dispatch on.

Usage:
    from core.intent_router import parse_intent
    env = parse_intent("/create new baza project foo type=dashboard")
    if env["intent"] == "create_baza_project":
        ...
"""
from __future__ import annotations

import re
import shlex
from typing import Any

INTENTS = (
    "create_baza_project",
    "create_ahb_project",
    "develop",
    "render",
    "preview",
    "test",
    "debug",
    "deploy",
    "iterate",
    "flash",
    "help",
)

_KV_RE = re.compile(r'(?P<k>[a-zA-Z_][a-zA-Z0-9_-]*)=(?P<v>"[^"]*"|\'[^\']*\'|\S+)')


def parse_intent(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    env: dict[str, Any] = {"intent": "unknown", "args": {}, "errors": [], "raw": raw}
    if not raw:
        env["errors"].append("empty input")
        return env

    body = raw.lstrip("/").strip()
    lower = body.lower()

    # Help — first because it's cheap
    if lower in ("help", "?", "commands"):
        env["intent"] = "help"
        return env

    # create new <baza|ahb> project [args]
    m = re.match(r'^(?:create\s+new|new|create)\s+(baza|ahb)\s+project\b\s*(.*)$', lower)
    if m:
        kind = m.group(1)
        # Re-extract from the original (case-preserving) tail
        tail_match = re.match(r'^(?:create\s+new|new|create)\s+(?:baza|ahb)\s+project\b\s*(.*)$', body, re.IGNORECASE)
        tail = tail_match.group(1) if tail_match else ""
        env["intent"] = "create_baza_project" if kind == "baza" else "create_ahb_project"
        env["args"] = _parse_create_args(tail, env["errors"])
        if not env["args"].get("name"):
            env["errors"].append("name is required")
        return env

    # Slot directives: <verb> <id> [extras...]
    SLOT_VERBS = {"develop", "render", "preview", "test", "debug", "deploy", "iterate", "flash"}
    parts = body.split(None, 2)
    if parts and parts[0].lower() in SLOT_VERBS:
        verb = parts[0].lower()
        if len(parts) < 2:
            env["intent"] = verb
            env["errors"].append("project_id is required")
            return env
        proj_id = parts[1]
        rest = parts[2] if len(parts) >= 3 else ""
        env["intent"] = verb
        env["args"] = {"project_id": proj_id}
        # Pull k=v args out of rest, treat the leftover as goal/instruction
        kvs, leftover = _extract_kvs(rest)
        env["args"].update(kvs)
        if leftover:
            env["args"]["goal"] = leftover
        # Mark privileged ones explicitly
        if verb in ("deploy", "flash"):
            env["args"]["privileged"] = True
            env["args"].setdefault("approved", False)
        return env

    env["errors"].append("unrecognized directive — try /help")
    return env


def _parse_create_args(tail: str, errors: list[str]) -> dict[str, Any]:
    """Parse `<positional name?> key=value …` from a create-project tail."""
    args: dict[str, Any] = {}
    kvs, leftover = _extract_kvs(tail)
    args.update(kvs)
    if leftover:
        # Strip surrounding quotes if any, treat as name
        name = leftover.strip().strip('"').strip("'")
        if name and "name" not in args:
            args["name"] = name
    # Aliases
    if "type" in args and args["type"]:
        args["type"] = str(args["type"]).lower()
    if "from" in args:
        args["from_chat"] = args.pop("from")
    return args


def _extract_kvs(text: str) -> tuple[dict[str, str], str]:
    """Pull key=value tokens out of `text`. Returns (kvs, leftover_string)."""
    if not text:
        return {}, ""
    kvs: dict[str, str] = {}
    consumed_spans: list[tuple[int, int]] = []
    for m in _KV_RE.finditer(text):
        v = m.group("v")
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        kvs[m.group("k").lower()] = v
        consumed_spans.append(m.span())
    if not consumed_spans:
        return {}, text.strip()
    # Build leftover by removing matched spans
    out_chars: list[str] = []
    last = 0
    for start, end in consumed_spans:
        out_chars.append(text[last:start])
        last = end
    out_chars.append(text[last:])
    leftover = re.sub(r'\s+', ' ', ''.join(out_chars)).strip()
    return kvs, leftover


def help_text() -> str:
    return (
        "Directives:\n"
        "  /create new baza project <name> [type=web-app|dashboard|library|esp-firmware|stm-firmware|lora-test|other]\n"
        "  /create new ahb project name=\"Kitchen Reno\" [from=<chat_id>]\n"
        "  /develop <id> <goal>          — assign work to an agent (requires #5)\n"
        "  /render <id>                  — generate visuals (requires #4.6)\n"
        "  /preview <id>                 — start preview server (requires #4.5)\n"
        "  /test <id>                    — run manifest test command\n"
        "  /debug <id>                   — view logs (requires #4.x)\n"
        "  /deploy <id> [target=<name>]  — privileged, requires approval\n"
        "  /flash  <id> [device=<dev>]   — privileged hardware flash\n"
        "  /iterate <id> <goal>          — agent iteration loop (requires #5)\n"
        "  /help                         — this list\n"
    )
