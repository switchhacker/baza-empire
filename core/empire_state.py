"""
Baza Empire — Situational Awareness

Builds the <EMPIRE_STATE> header injected into every agent's system_prompt
at boot, and provides the lookup primitives used by the self_orient skill.

Source of truth: EMPIRE_STATE.md at the framework root. Three default
sections (LIVE / KILLED / RECENT) plus arbitrary `## TOPIC: <slug>` blocks.

Token budget (deliberately tight — agents run on 8-32k context models):
  - Boot header: 4 KB hard cap. RECENT bullets truncated first if needed.
  - Per-topic lookup: 1.5 KB cap on skill output.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(FRAMEWORK_DIR, "EMPIRE_STATE.md")
BAZA_MAP = os.path.expanduser(
    "~/.claude/projects/-home-switchhacker/memory/baza-map.md"
)
SESSION_LOG = os.path.expanduser("~/Desktop/baza-session-log.md")

BOOT_HEADER_MAX_BYTES = 4096
SKILL_OUTPUT_MAX_BYTES = 1500

KNOWN_AGENTS = (
    "simon_bately", "claw_batto", "phil_hass", "sam_axe",
    "rex_valor", "duke_harmon", "scout_reeves", "nova_sterling",
    "specter_voss",
)

# Skills every agent should be aware of at boot. Other skills (200+) are
# discoverable via ##SKILL:self_orient{"topic":"myself"}## or list_tools.
ESSENTIAL_SKILLS = (
    "artifact_save", "briefing_data", "self_orient", "web_search",
    "web_fetch", "scrape_page", "update_task", "list_tools",
    "journal_log", "dispatch",
)


def _read_state_file() -> str:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def _extract_section(content: str, section_name: str) -> str:
    """Pull '## SECTION_NAME' to next '## ' header from EMPIRE_STATE.md content."""
    pat = re.compile(
        rf"^##\s+{re.escape(section_name)}\s*$(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(content)
    return m.group(1).strip() if m else ""


def _extract_topic(content: str, topic: str) -> str:
    """Pull '## TOPIC: <slug>' block (case-insensitive on slug)."""
    pat = re.compile(
        rf"^##\s+TOPIC:\s*{re.escape(topic)}\s*$(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(content)
    return m.group(1).strip() if m else ""


def list_topics() -> list[str]:
    """Return all topic slugs declared in EMPIRE_STATE.md."""
    content = _read_state_file()
    return [m.group(1).strip() for m in re.finditer(
        r"^##\s+TOPIC:\s*(.+?)\s*$", content, re.MULTILINE | re.IGNORECASE,
    )]


def get_agent_skills(agent_id: str) -> list[str]:
    """Return a flat list of skill names available to this agent.

    Sources: agents/<id>/skills/*.py (agent-specific) + skills/shared/*.py (all).
    """
    skills: set[str] = set()
    for base in (
        os.path.join(FRAMEWORK_DIR, "agents", agent_id, "skills"),
        os.path.join(FRAMEWORK_DIR, "skills", "shared"),
    ):
        if not os.path.isdir(base):
            continue
        for f in os.listdir(base):
            if f.endswith(".py") and not f.startswith("_"):
                skills.add(f[:-3])
    return sorted(skills)


def get_team_online() -> dict[str, bool]:
    """Map agent_id → True if its systemd unit is active. Best-effort."""
    out: dict[str, bool] = {}
    for ag in KNOWN_AGENTS:
        unit = f"baza-agent-{ag.replace('_', '-')}.service"
        try:
            r = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=2,
            )
            out[ag] = (r.stdout.strip() == "active")
        except Exception:
            out[ag] = False
    return out


def _format_team_online(team: dict[str, bool]) -> str:
    return " ".join(
        f"{ag.split('_')[0]}{'🟢' if up else '🔴'}" for ag, up in team.items()
    )


def get_agent_role(agent_id: str) -> str:
    """Best-effort agent role lookup from config/agents.yaml."""
    try:
        import yaml
        with open(os.path.join(FRAMEWORK_DIR, "config", "agents.yaml")) as f:
            cfg = yaml.safe_load(f) or {}
        node = (cfg.get("agents") or {}).get(agent_id) or cfg.get(agent_id) or {}
        return (
            node.get("role")
            or node.get("company_title")
            or node.get("name")
            or agent_id
        )
    except Exception:
        return agent_id


def build_header(agent_id: str) -> str:
    """Build the <EMPIRE_STATE> block to prepend to system_prompt at boot.

    Returns the empty string if EMPIRE_STATE.md is missing AND no fallback
    state is available — never raises.
    """
    state = _read_state_file()
    live = _extract_section(state, "LIVE")
    killed = _extract_section(state, "KILLED")
    recent = _extract_section(state, "RECENT")

    role = get_agent_role(agent_id)
    all_skills = get_agent_skills(agent_id)
    team = get_team_online()

    # Boot header surfaces only essential skills + count. Full list available
    # to the agent via ##SKILL:self_orient{"topic":"myself"}##.
    essential_present = [s for s in ESSENTIAL_SKILLS if s in all_skills]
    other_count = len(all_skills) - len(essential_present)
    skills_line = ", ".join(essential_present)
    if other_count > 0:
        skills_line += f"  (+{other_count} more — run self_orient[myself] to list)"

    parts = ["<EMPIRE_STATE>"]
    if live:
        parts.append(f"## LIVE\n{live}")
    if killed:
        parts.append(f"## KILLED\n{killed}")
    if recent:
        parts.append(f"## RECENT\n{recent}")
    parts.append(f"YOU ARE: {agent_id} — {role}")
    if skills_line:
        parts.append(f"YOUR SKILLS: {skills_line}")
    parts.append(f"TEAM ONLINE: {_format_team_online(team)}")
    parts.append(
        "If unsure about the current state of X, "
        "run ##SKILL:self_orient{\"topic\":\"X\"}## before responding. "
        "Do not invent facts about the empire — check first."
    )
    parts.append("</EMPIRE_STATE>")

    header = "\n\n".join(parts)
    if len(header.encode("utf-8")) <= BOOT_HEADER_MAX_BYTES:
        return header

    # Over budget — truncate RECENT bullets oldest-first, then KILLED, until fit.
    def _truncate_section(text: str, keep_lines: int) -> str:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[:keep_lines])

    for keep in (10, 7, 5, 3, 1, 0):
        recent_trim = _truncate_section(recent, keep)
        parts_t = ["<EMPIRE_STATE>"]
        if live:
            parts_t.append(f"## LIVE\n{live}")
        if killed:
            parts_t.append(f"## KILLED\n{killed}")
        if recent_trim:
            parts_t.append(f"## RECENT\n{recent_trim}")
        parts_t.append(f"YOU ARE: {agent_id} — {role}")
        if skills_line:
            parts_t.append(f"YOUR SKILLS: {skills_line}")
        parts_t.append(f"TEAM ONLINE: {_format_team_online(team)}")
        parts_t.append(
            "If unsure, run ##SKILL:self_orient{\"topic\":\"X\"}##."
        )
        parts_t.append("</EMPIRE_STATE>")
        candidate = "\n\n".join(parts_t)
        if len(candidate.encode("utf-8")) <= BOOT_HEADER_MAX_BYTES:
            return candidate
    # Last resort: identity-only stub.
    return (
        f"<EMPIRE_STATE>\nYOU ARE: {agent_id} — {role}\n"
        f"TEAM ONLINE: {_format_team_online(team)}\n</EMPIRE_STATE>"
    )


def lookup_topic(topic: str, *, with_session_log: bool = True) -> str:
    """Return the EMPIRE_STATE.md block for `topic`, plus recent session-log
    mentions if requested. Caps output at SKILL_OUTPUT_MAX_BYTES.

    Topic matching is case-insensitive and slug-tolerant ('ahb123' matches
    'ahb-123', 'ahb_123' etc.).
    """
    if not topic:
        return ""
    state = _read_state_file()
    block = _extract_topic(state, topic)
    if not block:
        # Tolerant match: try lower/upper/strip variants
        for variant in {topic.lower(), topic.upper(), topic.replace("-", " "),
                        topic.replace("_", " ")}:
            block = _extract_topic(state, variant)
            if block:
                break

    if not block:
        # Fall back to keyword grep across state file + baza-map.md
        hits: list[str] = []
        kw = topic.lower()
        for source_path in (STATE_FILE, BAZA_MAP):
            try:
                with open(source_path, encoding="utf-8") as f:
                    for ln in f:
                        if kw in ln.lower():
                            hits.append(f"  - {ln.strip()[:300]}")
                            if len(hits) >= 8:
                                break
            except Exception:
                pass
            if len(hits) >= 8:
                break
        if hits:
            block = (
                f"(no curated TOPIC block for '{topic}'; grep hits across "
                f"EMPIRE_STATE.md + baza-map.md:)\n" + "\n".join(hits[:8])
            )
        else:
            return (
                f"No information on topic '{topic}'. Try a broader keyword or "
                f"ask Serge to add a `## TOPIC: {topic}` block to EMPIRE_STATE.md."
            )

    out = f"## {topic.upper()}\n{block}"

    if with_session_log:
        recent = _grep_session_log(topic, max_hits=2)
        if recent:
            out += "\n\nRECENT MENTIONS IN SESSION LOG:\n" + "\n".join(recent)

    # Hard cap on output size
    if len(out.encode("utf-8")) > SKILL_OUTPUT_MAX_BYTES:
        out = out.encode("utf-8")[:SKILL_OUTPUT_MAX_BYTES].decode(
            "utf-8", errors="ignore"
        ) + "\n…[truncated]"
    return out


def _grep_session_log(topic: str, *, max_hits: int = 2) -> list[str]:
    """Return up to max_hits lines from session log mentioning `topic`."""
    if not os.path.isfile(SESSION_LOG):
        return []
    kw = topic.lower()
    hits: list[str] = []
    try:
        with open(SESSION_LOG, encoding="utf-8") as f:
            lines = f.readlines()
        # Walk newest-first
        for ln in reversed(lines):
            if kw in ln.lower() and ln.strip():
                hits.append(f"  • {ln.strip()[:200]}")
                if len(hits) >= max_hits:
                    break
    except Exception:
        pass
    return hits
