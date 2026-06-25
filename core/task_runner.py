#!/usr/bin/env python3
"""
Baza Empire — Autonomous Task Runner
100% local. No Base44. Runs independently via systemd timer or cron.

For each agent with pending/in_progress tasks:
  1. Fetch their tasks from local SQLite
  2. Send task to Ollama with the agent's persona
  3. Parse the output — extract deliverable + completion signal
  4. Mark task completed/in_progress in DB
  5. Save deliverable to tasks notes
  6. Notify Serge via Telegram with what got done

Usage:
  python core/task_runner.py                  # run all agents
  python core/task_runner.py --agent claw_batto  # run one agent
  python core/task_runner.py --dry-run        # show tasks, don't execute
"""
import os
import sys
import json
import logging
import sqlite3
import requests
import argparse
import yaml
import time
from datetime import datetime

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(FRAMEWORK_DIR, "configs", "secrets.env"))

from core.task_updater import (
    get_my_tasks, update_task, complete_task,
    start_task, get_project_stats, get_task_by_id
)
try:
    from core import task_events as _task_events  # visibility pipeline #1
except Exception:
    _task_events = None
from core import scaffold_config
try:
    # Skill executor — used to run ##SKILL:## calls the LLM emits during a task.
    # Safe to import at module load: context_db's connection pool is lazy.
    from core.skills_engine import SkillsEngine
except Exception:  # pragma: no cover — defensive; tasks still run, just no skills
    SkillsEngine = None


def _emit(kind: str, task: dict | None = None, agent_id: str | None = None,
          payload: dict | None = None, parent_event_id: int | None = None):
    """Best-effort emit. Never raises, never blocks the caller."""
    if _task_events is None:
        return None
    try:
        return _task_events.emit(
            kind,
            task_id=(task or {}).get("id"),
            project_id=(task or {}).get("project_id"),
            agent_id=agent_id,
            payload=payload or {},
            parent_event_id=parent_event_id,
        )
    except Exception:
        return None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TASK-RUNNER] %(message)s"
)
logger = logging.getLogger(__name__)

OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://localhost:11434")
# Cold-load of a 14B+ model over Vulkan can take 60-120s before tokens flow.
# Default 300s leaves headroom; override via env for tuning. A single retry on
# timeout reuses this same value (so worst-case wait = 2 * timeout).
LLM_REQUEST_TIMEOUT = int(os.getenv("BAZA_OLLAMA_REQUEST_TIMEOUT", "300"))
LLM_RETRY_ON_TIMEOUT = os.getenv("BAZA_OLLAMA_RETRY_ON_TIMEOUT", "1") not in ("0", "false", "no", "")
# Max times to re-prompt a single task within one cron tick when the LLM
# returned TASK_IN_PROGRESS. Each iteration feeds the prior output back so
# the agent can keep going. Cap exists to prevent runaway loops.
MAX_TASK_ITERATIONS = int(os.getenv("BAZA_MAX_TASK_ITERATIONS", "3"))


# Ollama instance pool. 2026-06-11: NVIDIA 3070 (11435) removed — it's now the
# dedicated Stable Diffusion image engine, so LLM stays on the AMD 6700 XT
# (11434 primary + 11437 secondary) + CPU. GPUs come before CPU because CPU
# inference of a 14B model exceeds the request timeout. CPU is reserved for
# small models (≤7B) only — see is_cpu_capable_model().
OLLAMA_INSTANCES_GPU = [
    os.getenv("OLLAMA_URL", "http://localhost:11434"),  # AMD primary
    "http://localhost:11437",                            # AMD secondary
]
OLLAMA_INSTANCES_CPU = ["http://localhost:11436"]


def is_cpu_capable_model(model: str) -> bool:
    """Whether a model is small enough to run reasonably on CPU."""
    m = (model or "").lower()
    if any(k in m for k in ("gemma3:1b", "gemma3:4b", "gemma4:e2b", "gemma4:e4b",
                            "llama3.2:1b", "llama3.2:3b", "qwen2.5:0.5b",
                            "qwen2.5:1.5b", "qwen2.5:3b", "qwen2.5:7b",
                            "phi3:mini", "phi3.5", "mistral:7b", "ministral")):
        return True
    return False


def instances_for_model(model: str) -> list[str]:
    instances = list(OLLAMA_INSTANCES_GPU)
    if is_cpu_capable_model(model):
        instances.append(OLLAMA_INSTANCES_CPU[0])
    return instances


# Back-compat — used for general "is the GPU pool free?" callers
OLLAMA_INSTANCES = OLLAMA_INSTANCES_GPU + OLLAMA_INSTANCES_CPU


def _instance_busy(url: str, timeout: int = 3) -> bool:
    try:
        r = requests.get(f"{url}/api/ps", timeout=timeout)
        if not r.ok:
            return True  # treat as busy if we can't poll
        return len(r.json().get("models", [])) > 0
    except Exception:
        return True


def pick_free_ollama(model: str | None = None) -> str | None:
    """Return URL of the first free Ollama instance suitable for this model.
    Big models skip the CPU fallback entirely."""
    for url in instances_for_model(model or ""):
        if not _instance_busy(url):
            return url
    return None


def is_ollama_busy(timeout: int = 3) -> bool:
    """True only if ALL GPU instances are busy (CPU not considered)."""
    return all(_instance_busy(u) for u in OLLAMA_INSTANCES_GPU)


def wait_for_ollama(max_wait: int = 120, model: str | None = None) -> str | None:
    """Wait until at least one suitable Ollama instance is free. Returns URL or None."""
    waited = 0
    while waited < max_wait:
        free = pick_free_ollama(model)
        if free:
            if free != OLLAMA_INSTANCES_GPU[0]:
                logger.info(f"  Routing to fallback Ollama: {free}")
            return free
        logger.info(f"  All Ollama instances busy — waiting... ({waited}s)")
        time.sleep(10)
        waited += 10
    logger.warning(f"  All Ollama instances still busy after {max_wait}s — skipping task")
    return None
TELEGRAM_TOKEN = os.getenv("TELEGRAM_SIMON_BATELY")
SERGE_CHAT_ID  = os.getenv("SERGE_CHAT_ID", "8551331144")
DB_PATH        = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
CONFIG_PATH    = os.path.join(FRAMEWORK_DIR, "config", "agents.yaml")

# Tasks with these keywords are deliverable by LLM — others need human/tool
LLM_ACTIONABLE = [
    "content", "copy", "write", "draft", "page", "script",
    "document", "policy", "terms", "agreement", "template",
    "research", "competitor", "analysis", "plan", "workflow",
    "process", "logo", "brand", "color", "typography", "icon",
    "brief", "proposal", "email", "intake", "qualification",
    "pipeline", "lead", "invoice", "voicemail", "countdown",
]


def load_agent_configs() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("agents", {})
    except Exception as e:
        logger.error(f"Could not load agents.yaml: {e}")
        return {}


def is_llm_actionable(task: dict) -> bool:
    title = (task.get("title", "") + " " + task.get("description", "")).lower()
    return any(kw in title for kw in LLM_ACTIONABLE)


def _run_skills_and_reformat(agent_id: str, task: dict, output: str,
                             model: str, system: str, target_url: str) -> str:
    """Execute ##SKILL:## calls in `output` (except artifact_save) and, if any
    succeeded, re-prompt the LLM with the real skill data so the final
    deliverable is grounded in fact rather than simulated.

    Returns the (possibly updated) output. Best-effort: on any error it falls
    back to the original/spliced text so a task never dies on skill plumbing.
    `artifact_save` is excluded — it's handled by _execute_skill_saves() to
    avoid double-saving."""
    if SkillsEngine is None:
        return output
    try:
        engine = SkillsEngine(agent_id)
        spliced, skill_results = engine.parse_and_run(
            output,
            task_id=task.get("id"),
            project_id=task.get("project_id"),
            exclude={"artifact_save"},
        )
    except Exception as e:
        logger.warning(f"  Skill execution unavailable/failed: {e}")
        return output

    successful = [r for r in skill_results if r.get("success")]
    if not successful:
        # No skills ran, or all failed. If markers were present, return the
        # spliced text (which surfaces [SKILL ERROR: ...] honestly) rather than
        # the raw markers; otherwise return the original output unchanged.
        return spliced if skill_results else output

    skill_data = "\n\n".join(
        f"[{r.get('skill', 'skill')} output]\n{r.get('output', '')}" for r in successful
    )
    proj_id = task.get("project_id", "shared")
    reformat_user = (
        f"Task: {task['title']}\n"
        f"Description: {task.get('description', '')}\n\n"
        f"Here is the REAL live data returned by your skills:\n\n{skill_data}\n\n"
        "Now produce the final deliverable using ONLY this real data. "
        "Do NOT invent, estimate, or simulate any values, URLs, or figures.\n"
        "Save the deliverable with "
        f'##SKILL:artifact_save{{"filename":"deliverable.md","content":"...","project_id":"{proj_id}"}}## '
        "then write TASK_COMPLETE on its own line (or TASK_IN_PROGRESS if more work remains)."
    )
    payload = {
        "model": model,
        "stream": False,
        "options": {"num_predict": 2000, "temperature": 0.3},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": reformat_user},
        ],
    }
    try:
        resp = requests.post(f"{target_url}/api/chat", json=payload, timeout=LLM_REQUEST_TIMEOUT)
        resp.raise_for_status()
        reformatted = resp.json()["message"]["content"].strip()
        if reformatted:
            logger.info(f"  🔎 {len(successful)} skill(s) ran — regrounded deliverable on real data")
            return reformatted
    except Exception as e:
        logger.warning(f"  Skill reformat pass failed: {e} — using spliced output")
    # Fallback: spliced text already contains the real [SKILL RESULT] data inline.
    return spliced


def _run_scaffold_loop(agent_id: str, task: dict, system: str, user_msg: str,
                       model: str, target_url: str) -> str:
    """Scaffold path: select relevant skills, then run the bounded
    plan→act→observe→finish loop against Ollama. Generalizes the single-call +
    _run_skills_and_reformat two-pass to N steps. Returns the final output text.
    Raises on hard failure so the caller can fall back to the legacy path."""
    from core import agent_loop, skill_selector
    engine = SkillsEngine(agent_id)
    sel = skill_selector.select(
        f"{task['title']} {task.get('description', '')}", agent_id=agent_id,
        pinned=scaffold_config.pinned_core(), role_pins=[],
        top_k=scaffold_config.retrieval_top_k())
    system_with_skills = system + "\n\n" + skill_selector.render_block(sel)

    def _llm(messages, system_prompt):
        payload = {"model": model, "stream": False,
                   "options": {"num_predict": 2000, "temperature": 0.3},
                   "messages": [{"role": "system", "content": system_prompt}] + messages}
        r = requests.post(f"{target_url}/api/chat", json=payload, timeout=LLM_REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    res = agent_loop.run_loop(
        _llm, engine, system=system_with_skills, user=user_msg,
        max_steps=scaffold_config.max_steps(),
        parse_kwargs={"task_id": task.get("id"), "project_id": task.get("project_id"),
                      "exclude": {"artifact_save"}})
    return res["final"]


def run_task_with_llm(agent_id: str, agent_cfg: dict, task: dict, prior_output: str = "", ollama_url: str | None = None) -> dict:
    """
    Send a task to Ollama with the agent's persona.
    Returns {"success": bool, "output": str, "completed": bool}

    `prior_output` carries forward the previous iteration's response so the
    agent can pick up where it left off when iterating on TASK_IN_PROGRESS.
    """
    model       = agent_cfg.get("model", "qwen2.5:14b")
    agent_name  = agent_cfg.get("name", agent_id)
    system_base = agent_cfg.get("system_prompt", f"You are {agent_name}.")

    proj_id = task.get("project_id", "shared")
    system = (
        f"{system_base}\n\n"
        "TASK EXECUTION MODE — AUTONOMOUS:\n"
        "You have been assigned a task. Execute it FULLY and produce the real deliverable.\n"
        "Do not ask for clarification. If something is ambiguous, pick the most\n"
        "probable interpretation, document the assumption inline, and continue.\n"
        "If you genuinely lack a hard prerequisite (credentials, external access),\n"
        "use TASK_BLOCKED with a one-line reason. Otherwise: keep going.\n\n"
        "MANDATORY: Save ALL deliverables as artifacts using this exact syntax:\n"
        f"  ##SKILL:artifact_save{{\"filename\":\"deliverable.md\",\"content\":\"...\",\"project_id\":\"{proj_id}\"}}##\n"
        "For research tasks: save research notes, then a plan, then the final report — all as separate artifacts.\n"
        "For documents: save as .md or .html. For code: save as .py/.sh/.js. For data: save as .json or .csv.\n"
        "Use full markdown, headers, and code blocks inside artifact content — that is what artifacts are for.\n\n"
        "After saving all artifacts, write exactly one of these on its own line:\n"
        "  TASK_COMPLETE — if the task is fully done\n"
        "  TASK_IN_PROGRESS — if you made progress but need more work\n"
        "  TASK_BLOCKED: [reason] — if you cannot proceed\n\n"
        "Plain text only in chat summary. No markdown headers. No ** bold. Use emoji for structure."
    )

    if prior_output:
        user_msg = (
            f"You are continuing this task. Here is what you produced so far:\n\n"
            f"--- PRIOR OUTPUT ---\n{prior_output[:6000]}\n--- END PRIOR ---\n\n"
            f"Now finish the task. If everything is already done, write TASK_COMPLETE.\n"
            f"Otherwise, do the next concrete step that drives toward completion.\n\n"
            f"Task title: {task['title']}\n"
            f"Description: {task.get('description', '')}\n"
        )
    else:
        user_msg = (
            f"Execute this task now:\n\n"
            f"Title: {task['title']}\n"
            f"Description: {task.get('description', '')}\n"
            f"Due: {task.get('due_date', 'ASAP')}\n"
            f"Priority: {task.get('priority', 'medium')}\n\n"
            f"Produce the full deliverable. Be specific and complete."
        )

    payload = {
        "model": model,
        "stream": False,
        "options": {"num_predict": 2000, "temperature": 0.3},
        "messages": [
            {"role": "system",  "content": system},
            {"role": "user",    "content": user_msg},
        ],
    }

    target_url = ollama_url or OLLAMA_URL
    output = ""

    # Scaffold path subsumes the legacy single-call + _run_skills_and_reformat
    # two-pass: agent_loop runs skills inline across steps (artifact_save still
    # excluded). On ANY failure (incl. a cold-model ReadTimeout on the first
    # step) we fall back to the legacy path below, which keeps its own retry.
    use_legacy = True
    if scaffold_config.is_enabled(agent_id) and SkillsEngine is not None:
        try:
            output = _run_scaffold_loop(agent_id, task, system, user_msg, model, target_url)
            use_legacy = False
        except Exception as e:
            logger.warning(
                f"[scaffold] loop failed for {agent_id} task {task['id'][:8]}: {e} — using legacy")
            use_legacy = True

    if use_legacy:
        attempts = 2 if LLM_RETRY_ON_TIMEOUT else 1
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                resp = requests.post(
                    f"{target_url}/api/chat",
                    json=payload,
                    timeout=LLM_REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                output = resp.json()["message"]["content"].strip()
                last_err = None
                break
            except requests.exceptions.ReadTimeout as e:
                last_err = e
                logger.warning(
                    f"Ollama read-timeout for {agent_id} task {task['id'][:8]} "
                    f"after {LLM_REQUEST_TIMEOUT}s (attempt {attempt}/{attempts})"
                )
                # On first timeout, give cold-loaded model a moment before retry
                if attempt < attempts:
                    time.sleep(5)
                    continue
            except Exception as e:
                last_err = e
                break  # non-timeout errors don't benefit from retry

        if last_err is not None:
            logger.error(f"LLM error for {agent_id} task {task['id'][:8]}: {last_err}")
            return {"success": False, "output": str(last_err), "completed": False}

        # ── Skills: execute any ##SKILL:## calls the LLM emitted, then re-prompt
        # with the REAL data so the agent grounds its deliverable instead of
        # simulating results. `artifact_save` is excluded — it's handled separately
        # by _execute_skill_saves() so we don't double-save.
        output = _run_skills_and_reformat(
            agent_id, task, output, model, system, target_url
        )

    try:

        # Parse completion signal
        completed  = "TASK_COMPLETE" in output
        blocked    = "TASK_BLOCKED:" in output
        in_progress = "TASK_IN_PROGRESS" in output

        # Extract blocked reason if any
        block_reason = ""
        if blocked:
            for line in output.split("\n"):
                if "TASK_BLOCKED:" in line:
                    block_reason = line.split("TASK_BLOCKED:", 1)[-1].strip()
                    break

        # Clean signal lines from output before saving as notes
        clean_output = "\n".join(
            line for line in output.split("\n")
            if not any(sig in line for sig in ["TASK_COMPLETE", "TASK_IN_PROGRESS", "TASK_BLOCKED:"])
        ).strip()

        return {
            "success":      True,
            "output":       clean_output,
            "completed":    completed,
            "in_progress":  in_progress,
            "blocked":      blocked,
            "block_reason": block_reason,
        }

    except Exception as e:
        # task_error event is emitted by the upstream "else" branch in
        # run_agent_tasks() so we don't duplicate it here.
        logger.error(f"LLM parse error for {agent_id} task {task['id'][:8]}: {e}")
        return {"success": False, "output": str(e), "completed": False}


def notify_serge(message: str):
    if not TELEGRAM_TOKEN:
        logger.warning("No Telegram token — skipping notify")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            requests.post(url, json={"chat_id": SERGE_CHAT_ID, "text": chunk}, timeout=15)
    except Exception as e:
        logger.error(f"Telegram notify error: {e}")



def _parse_artifact_args(block: str, agent_id: str = "agent") -> dict | None:
    """Tolerantly parse an artifact_save `{...}` block.

    LLM-emitted JSON with a big free-text `content` field is fragile: models put
    literal newlines (rejected by strict JSON) AND unescaped double-quotes
    (e.g. a quoted product name) inside it, which breaks json.loads entirely.
    Strategy: try lenient JSON first; on failure, positionally recover
    filename/content/project_id so the real deliverable is never lost."""
    import re as _re
    try:
        args = json.loads(block, strict=False)  # strict=False allows literal control chars
        if isinstance(args, dict):
            return args
    except Exception:
        pass

    def _simple(name):
        m = _re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % name, block)
        return m.group(1) if m else None

    cm = _re.search(r'"content"\s*:\s*"', block)
    if not cm:
        return None
    tail = block[cm.end():]
    # content ends at the next structural key (",\"project_id\"|\"filename\":")
    # or, failing that, the final closing "}.
    term = _re.search(r'"\s*,\s*"(?:project_id|filename)"\s*:', tail)
    if term:
        content = tail[:term.start()]
    else:
        bm = _re.search(r'"\s*\}\s*$', tail)
        content = tail[:bm.start()] if bm else tail
    # Minimal unescape of recovered raw content (backslash first, via sentinel).
    content = (content.replace('\\\\', '\x00')
                      .replace('\\"', '"').replace('\\n', '\n')
                      .replace('\\t', '\t').replace('\\r', '\r')
                      .replace('\x00', '\\'))
    return {
        "filename": _simple("filename") or f"{agent_id}_output.md",
        "content": content,
        "project_id": _simple("project_id") or "shared",
    }


def _execute_skill_saves(agent_id: str, output: str) -> int:
    """
    Parse ##SKILL:artifact_save{...}## calls from LLM output and execute them via the dashboard API.
    Returns count of successful saves. Called before the fallback _save_artifact.
    """
    import re as _re
    pattern = _re.compile(r'##SKILL:\s*artifact_save\s*(\{.*?\})\s*##', _re.DOTALL)
    saved = 0
    for match in pattern.finditer(output):
        try:
            args = _parse_artifact_args(match.group(1), agent_id)
            if not args:
                logger.warning("  Skill-save parse error: could not extract artifact args")
                continue
            sys.path.insert(0, FRAMEWORK_DIR)
            from skills.shared.save_artifact import save_artifact as _api_save
            result = _api_save(
                filename=args.get('filename', f'{agent_id}_output.md'),
                content=args.get('content', ''),
                project_id=args.get('project_id', 'shared'),
                agent_id=agent_id,
            )
            if result.get('success'):
                saved += 1
                logger.info(f"  📁 Skill-save: {args.get('filename')}")
                if _task_events is not None:
                    _task_events.emit(
                        "artifact_saved",
                        agent_id=agent_id,
                        project_id=args.get('project_id', 'shared'),
                        payload={
                            "path": result.get('path') or args.get('filename', ''),
                            "filename": args.get('filename', ''),
                            "kind": "skill_save",
                            "bytes": len(args.get('content', '') or ''),
                        },
                    )
            else:
                logger.warning(f"  Skill-save failed: {result.get('error','')}")
        except Exception as e:
            logger.warning(f"  Skill-save parse error: {e}")
    return saved


def _save_artifact(agent_id: str, task: dict, content: str):
    """Save completed task deliverable via the dashboard API (fallback: direct filesystem)."""
    import re as _re
    try:
        sys.path.insert(0, FRAMEWORK_DIR)
        from skills.shared.save_artifact import save_artifact as _api_save, detect_project
        proj_id = task.get("project_id") or detect_project(content)
        title   = _re.sub(r'[^\w]', '_', task.get("title", "artifact"))[:40].strip('_')
        ts      = datetime.now().strftime("%Y%m%d_%H%M")

        # Detect appropriate extension from content
        code_blocks = _re.findall(r'```(\w*)\n', content)
        if code_blocks:
            lang = code_blocks[0].lower()
            ext = {
                'python': 'py', 'py': 'py', 'bash': 'sh', 'sh': 'sh',
                'javascript': 'js', 'js': 'js', 'html': 'html',
                'json': 'json', 'yaml': 'yml', 'yml': 'yml', 'sql': 'sql',
            }.get(lang, 'md')
        elif bool(_re.search(r'^#+\s', content, _re.MULTILINE)):
            ext = 'md'
        else:
            ext = 'md'

        filename = f"{agent_id}_{ts}_{title}.{ext}"
        result = _api_save(
            filename=filename,
            content=content,
            project_id=proj_id,
            agent_id=agent_id,
            task_id=task.get("id", ""),
        )
        if result.get("success"):
            logger.info(f"  📁 Artifact saved: {filename} → {proj_id}")
            _emit("artifact_saved", task=task, agent_id=agent_id,
                  payload={
                      "path": result.get('path') or filename,
                      "filename": filename, "kind": "task_deliverable",
                      "bytes": len((content or '')),
                      "project_id": proj_id,
                  })
        else:
            raise Exception(result.get("error", "API error"))
    except Exception as e:
        # Fallback: write directly to filesystem if API is unavailable
        try:
            proj_id  = task.get("project_id", "shared")
            title    = task.get("title", "artifact").replace("/", "-").replace(" ", "_")[:40]
            ts       = datetime.now().strftime("%Y%m%d_%H%M")
            filename = f"{agent_id}_{ts}_{title}.md"
            art_dir  = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts", proj_id)
            os.makedirs(art_dir, exist_ok=True)
            with open(os.path.join(art_dir, filename), "w", encoding="utf-8") as f:
                f.write(f"# Task: {task.get('title')}\n\n")
                f.write(f"Agent: {agent_id}  \nCompleted: {datetime.now().isoformat()}\n\n---\n\n")
                f.write(content)
            logger.info(f"  📁 Artifact saved (direct): {filename}")
            _emit("artifact_saved", task=task, agent_id=agent_id,
                  payload={
                      "path": os.path.join("dashboard", "artifacts", proj_id, filename),
                      "filename": filename, "kind": "task_deliverable_fallback",
                      "bytes": len((content or '')),
                  })
        except Exception as e2:
            logger.warning(f"  Artifact save failed: {e2}")


def _ensure_depends_on_column():
    """Add depends_on column to tasks table if it doesn't exist (idempotent)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("ALTER TABLE tasks ADD COLUMN depends_on TEXT DEFAULT ''")
        conn.commit()
        conn.close()
    except Exception:
        pass  # Column already exists


def check_dependencies(task: dict) -> bool:
    """Check if all dependency tasks are completed. Returns True if ready to run."""
    depends = task.get('depends_on', '') or ''
    if not depends:
        return True
    dep_ids = [d.strip() for d in depends.split(',') if d.strip()]
    for dep_id in dep_ids:
        dep_task = get_task_by_id(dep_id)
        if dep_task and dep_task.get('status') != 'completed':
            return False
    return True


def run_agent_tasks(agent_id: str, agent_cfg: dict, dry_run: bool = False, task_id: str = None) -> list:
    """Run all pending tasks for one agent. Returns list of result dicts."""
    if task_id:
        # Single task mode — fetch just this task
        from core.task_updater import get_task_by_id
        t = get_task_by_id(task_id)
        tasks = [t] if t else []
    else:
        tasks = get_my_tasks(agent_id, status="pending")
        # Add in_progress only if not already in list (avoid duplicates)
        in_prog = get_my_tasks(agent_id, status="in_progress")
        existing_ids = {t["id"] for t in tasks}
        tasks += [t for t in in_prog if t["id"] not in existing_ids]

    # Filter out completed tasks — never re-run them
    tasks = [t for t in tasks if t.get("status") != "completed"]

    if not tasks:
        logger.info(f"[{agent_id}] No tasks to run.")
        return []

    agent_name = agent_cfg.get("name", agent_id)
    results = []

    for task in tasks:
        task_id    = task["id"]
        task_title = task["title"]

        logger.info(f"[{agent_id}] Task [{task_id}]: {task_title[:60]}")

        # Check task dependencies before running
        if not check_dependencies(task):
            logger.info(f"  Skipping {task_title[:50]} — dependencies not met")
            results.append({"task": task_title, "status": "waiting_on_deps"})
            continue

        if dry_run:
            actionable = is_llm_actionable(task)
            logger.info(f"  DRY RUN — actionable: {actionable}")
            results.append({"task": task_title, "dry_run": True, "actionable": actionable})
            continue

        if not is_llm_actionable(task):
            logger.info(f"  Skipping non-LLM task: {task_title[:50]}")
            # Mark in_progress so it shows activity
            start_task(task_id, notes="Requires external action or tool — marked in progress")
            continue

        # Wait for any Ollama instance suitable for this agent's model
        agent_model = (agent_cfg.get("model") or "qwen2.5:14b")
        ollama_url = wait_for_ollama(max_wait=120, model=agent_model)
        if not ollama_url:
            logger.warning(f"  Skipping {task_title[:40]} — all Ollama instances busy")
            results.append({"task": task_title, "status": "skipped"})
            continue

        # Mark in_progress before running
        start_task(task_id)
        _emit("task_started", task=task, agent_id=agent_id,
              payload={"title": task_title})

        # Iterate: run, and if LLM said TASK_IN_PROGRESS, re-prompt up to
        # MAX_TASK_ITERATIONS times feeding prior output back. Stops on
        # TASK_COMPLETE, TASK_BLOCKED, or LLM failure.
        result = run_task_with_llm(agent_id, agent_cfg, task, ollama_url=ollama_url)
        accumulated = result.get("output", "")
        iterations = 1
        while (
            result.get("success")
            and result.get("in_progress")
            and not result.get("completed")
            and not result.get("blocked")
            and iterations < MAX_TASK_ITERATIONS
        ):
            iterations += 1
            logger.info(f"  ↻ ITERATING {task_title[:50]} ({iterations}/{MAX_TASK_ITERATIONS})")
            _emit("task_progress", task=task, agent_id=agent_id,
                  payload={"notes_snippet": (accumulated or "")[:300],
                           "iteration": iterations})
            # Brief breath so we don't hammer Ollama back-to-back
            time.sleep(2)
            result = run_task_with_llm(agent_id, agent_cfg, task, prior_output=accumulated, ollama_url=ollama_url)
            if result.get("success"):
                accumulated = (accumulated + "\n\n--- next iteration ---\n\n"
                               + (result.get("output") or ""))[-12000:]
                # Make later branches see the merged transcript
                result["output"] = accumulated

        if result["success"]:
            # Save output as notes (truncated to fit DB)
            notes = result["output"][:500]

            if result["completed"]:
                complete_task(task_id, notes=notes)
                logger.info(f"  ✅ COMPLETED: {task_title[:50]}")
                _emit("task_completed", task=task, agent_id=agent_id,
                      payload={"notes_snippet": (notes or "")[:600]})
                try:
                    from core.event_bus import publish_sync
                    publish_sync(agent_id, "task_completed", {
                        "task_id": task_id, "title": task_title,
                        "result": notes[:200] if notes else ""
                    })
                except Exception:
                    pass
                try:
                    from core.context_db import journal_log
                    journal_log(agent_id=agent_id, task_type="task_completed",
                                task_description=task_title,
                                action_summary=f"{agent_id.replace('_',' ').title()} completed: {task_title[:100]}",
                                requested_by="task_runner", status="completed",
                                result=notes[:500])
                except Exception:
                    pass
                # Execute any ##SKILL:artifact_save## calls from LLM output first
                skill_saves = _execute_skill_saves(agent_id, result["output"])
                # Fallback: save full deliverable as artifact if LLM didn't save any
                if skill_saves == 0:
                    _save_artifact(agent_id, task, result["output"])
                # Process any DISPATCH lines in agent output — forward to target agents
                dispatched = process_dispatch_lines(result["output"], agent_id)
                results.append({
                    "task": task_title,
                    "status": "completed",
                    "output": notes,
                    "project_id": task.get("project_id", ""),
                    "dispatched": dispatched,
                })

            elif result["blocked"]:
                update_task(task_id, {
                    "status": "blocked",
                    "notes": f"BLOCKED: {result['block_reason']}"
                })
                logger.info(f"  🔴 BLOCKED: {task_title[:50]} — {result['block_reason']}")
                _emit("task_blocked", task=task, agent_id=agent_id,
                      payload={"reason": (result.get("block_reason") or "")[:600]})
                results.append({"task": task_title, "status": "blocked", "reason": result["block_reason"]})
                try:
                    from core.context_db import journal_log
                    journal_log(agent_id=agent_id, task_type="task_blocked",
                                task_description=task_title,
                                action_summary=f"{agent_id.replace('_',' ').title()} blocked: {task_title[:80]} — {result['block_reason'][:60]}",
                                requested_by="task_runner", status="blocked")
                except Exception:
                    pass

            else:
                update_task(task_id, {"status": "in_progress", "notes": notes})
                logger.info(f"  🟡 IN PROGRESS: {task_title[:50]}")
                _emit("task_progress", task=task, agent_id=agent_id,
                      payload={"notes_snippet": (notes or "")[:600]})
                results.append({"task": task_title, "status": "in_progress", "output": notes})
        else:
            logger.error(f"  LLM failed for {task_title[:50]}: {result['output'][:100]}")
            _emit("task_error", task=task, agent_id=agent_id,
                  payload={"error": (result.get("output") or "")[:500]})
            results.append({"task": task_title, "status": "error", "output": result["output"]})

        # Brief pause between tasks so Ollama isn't hammered
        time.sleep(5)

    return results


def notify_agent(agent_id: str, message: str):
    """Send a Telegram message via an agent's bot to Serge's chat."""
    token_env_map = {
        "phil_hass":     "TELEGRAM_PHIL_HASS",
        "claw_batto":    "TELEGRAM_CLAW_BATTO",
        "sam_axe":       "TELEGRAM_SAM_AXE",
        "nova_sterling": "TELEGRAM_NOVA_STERLING",
        "rex_valor":     "TELEGRAM_REX_VALOR",
        "duke_harmon":   "TELEGRAM_DUKE_HARMON",
        "scout_reeves":  "TELEGRAM_SCOUT_REEVES",
    }
    token = os.getenv(token_env_map.get(agent_id, ""))
    if not token:
        logger.warning(f"No token for {agent_id} — cannot ping")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": SERGE_CHAT_ID, "text": message},
            timeout=10
        )
        logger.info(f"  📤 Pinged {agent_id}")
    except Exception as e:
        logger.warning(f"  Ping {agent_id} failed: {e}")


def process_dispatch_lines(output: str, source_agent: str):
    """
    Parse DISPATCH:agent_id:instruction lines from task output.
    Sends each dispatch as a Telegram message via that agent's bot.
    """
    dispatched = []
    for line in output.split("\n"):
        line = line.strip()
        if line.upper().startswith("DISPATCH:"):
            parts = line.split(":", 2)
            if len(parts) == 3:
                target = parts[1].strip().lower()
                instruction = parts[2].strip()
                msg = f"📌 DISPATCH from {source_agent}:\n{instruction}"
                notify_agent(target, msg)
                dispatched.append(target)
                logger.info(f"  DISPATCH → {target}: {instruction[:60]}")
                if _task_events is not None:
                    _task_events.emit(
                        "dispatch_sent",
                        agent_id=source_agent,
                        payload={
                            "to_agent": target,
                            "instruction_snippet": instruction[:600],
                        },
                    )
    return dispatched


def build_summary_message(all_results: dict) -> str:
    """Build Telegram notification with what got done."""
    stats = get_project_stats()
    now   = datetime.now().strftime("%I:%M %p")

    lines = [
        f"━━━━━━━━━━━━━━━━",
        f"⚡ Task Runner — {now}",
        f"━━━━━━━━━━━━━━━━",
        f"📊 {stats['progress_pct']}% done ({stats['completed']}/{stats['total']} tasks)",
        "",
    ]

    name_map = {
        "claw_batto": "Claw", "sam_axe": "Sam", "phil_hass": "Phil",
        "simon_bately": "Simon", "duke_harmon": "Duke", "rex_valor": "Rex",
        "scout_reeves": "Scout", "nova_sterling": "Nova",
    }

    artifacts_base = os.path.join(FRAMEWORK_DIR, "dashboard", "artifacts")

    for agent_id, results in all_results.items():
        if not results:
            continue
        name = name_map.get(agent_id, agent_id)
        completed   = [r for r in results if r.get("status") == "completed"]
        blocked     = [r for r in results if r.get("status") == "blocked"]
        in_progress = [r for r in results if r.get("status") == "in_progress"]

        if completed or blocked or in_progress:
            lines.append(f"👤 {name}:")
            for r in completed:
                lines.append(f"  ✅ {r['task'][:55]}")
                # List any artifacts saved for this task
                proj_id = r.get("project_id", "")
                if proj_id:
                    art_dir = os.path.join(artifacts_base, proj_id)
                    if os.path.isdir(art_dir):
                        recent = sorted(
                            [f for f in os.listdir(art_dir) if agent_id[:4] in f],
                            key=lambda f: os.path.getmtime(os.path.join(art_dir, f)),
                            reverse=True
                        )[:2]
                        for af in recent:
                            lines.append(f"     📎 {af}")
            for r in blocked:
                lines.append(f"  🔴 {r['task'][:40]} — {r.get('reason','')[:40]}")
            for r in in_progress:
                lines.append(f"  🟡 {r['task'][:55]}")
            lines.append("")

    if stats["blocked"] > 0:
        lines.append(f"⚠️ {stats['blocked']} task(s) blocked — check dashboard")
    lines.append(f"📋 Dashboard: http://localhost:8888")
    lines.append("━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent",   help="Run only this agent (e.g. claw_batto)")
    parser.add_argument("--task-id", help="Run only this specific task ID")
    parser.add_argument("--dry-run", action="store_true", help="Show tasks without executing")
    args = parser.parse_args()

    # Ensure depends_on column exists in tasks table
    _ensure_depends_on_column()

    agents = load_agent_configs()
    if not agents:
        logger.error("No agents found in config — aborting")
        sys.exit(1)

    if args.agent:
        if args.agent not in agents:
            logger.error(f"Agent '{args.agent}' not found in config")
            sys.exit(1)
        agents = {args.agent: agents[args.agent]}

    all_results = {}
    for agent_id, agent_cfg in agents.items():
        logger.info(f"Running tasks for: {agent_id}")
        results = run_agent_tasks(agent_id, agent_cfg, dry_run=args.dry_run, task_id=getattr(args, 'task_id', None))
        all_results[agent_id] = results

    if not args.dry_run:
        # Only notify if something actually happened
        any_results = any(r for r in all_results.values())
        if any_results:
            msg = build_summary_message(all_results)
            notify_serge(msg)
            logger.info("Summary sent to Serge.")
        else:
            logger.info("No tasks ran — nothing to notify.")


if __name__ == "__main__":
    main()
