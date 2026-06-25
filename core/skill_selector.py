"""Per-request skill/tool selection. Given a message + agent, return the set of
skills to put in front of the LLM: pinned core + agent role-pins + top-K FTS
retrieved, plus a category index. Rendered into a compact prompt block."""
from core import skill_registry as reg


def select(message: str, agent_id: str | None = None,
           pinned: list[str] | None = None, role_pins: list[str] | None = None,
           top_k: int = 8, json_path: str = reg.DEFAULT_JSON,
           db_path: str = reg.DEFAULT_DB) -> dict:
    pinned = pinned or []
    role_pins = role_pins or []
    chosen: dict[str, dict] = {}

    def _add(name: str):
        if name in chosen:
            return
        d = reg.get(name, json_path=json_path)
        if d:
            chosen[name] = d

    for n in pinned:
        _add(n)
    for n in role_pins:
        _add(n)
    if message.strip():
        for hit in reg.search(message, db_path=db_path, top_k=top_k):
            if hit["name"] in chosen:
                continue
            # FTS rows omit `args`; enrich from the manifest so the rendered
            # call block can show arg hints for retrieved skills.
            chosen[hit["name"]] = reg.get(hit["name"], json_path=json_path) or hit

    return {
        "skills": list(chosen.values()),
        "categories": reg.categories(json_path=json_path),
        "agent_id": agent_id,
    }


def render_block(selection: dict) -> str:
    lines = ["== RELEVANT SKILLS FOR THIS REQUEST =="]
    for s in selection["skills"]:
        args = s.get("args") or {}
        arg_hint = ", ".join(f'"{k}":<{v}>' for k, v in list(args.items())[:4]) if args else ""
        call = f'##SKILL:{s["name"]}{{{arg_hint}}}##' if s.get("type") == "skill" \
            else f'##SKILL:call_tool{{"agent":"{args.get("agent","")}",' \
                 f'"tool":"{args.get("tool","")}","input":{{}}}}##'
        summ = s.get("summary", "")
        when = f" — {s['when_to_use']}" if s.get("when_to_use") else ""
        lines.append(f"{call}\n    {summ}{when}")
    cats = selection.get("categories", {})
    if cats:
        cat_str = ", ".join(f"{c}({n})" for c, n in sorted(cats.items()))
        lines.append(f"\nYou also have skills in: {cat_str}.")
        lines.append('Call ##SKILL:skill_search{"query":"..."}## to discover more skills mid-task.')
    lines.append("== END RELEVANT SKILLS ==")
    return "\n".join(lines)
