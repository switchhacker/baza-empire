import os, re, json, subprocess, logging, time
from typing import Optional
from core.context_db import get_skills, skill_ran, journal_log
try:
    from core import task_events as _task_events  # visibility pipeline #1
except Exception:  # pragma: no cover — defensive; pipeline is best-effort
    _task_events = None
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_SHARED_DIR = os.path.join(BASE_DIR, "skills", "shared")
# Start-of-call pattern: ##SKILL:name followed by optional {json-args} and an optional closing ##.
# We do brace-balanced scanning manually because regex can't handle nested braces inside JSON values.
SKILL_NAME_PATTERN = re.compile(r'##SKILL:\s*(\w[\w\-]+)\s*')


def _extract_json_block(text: str, start: int) -> tuple[str, int]:
    """Starting at the first '{' on/after `start`, return (json_substring, end_index_after_closing_brace).
    Returns ('', start) if no '{' at that position. Tracks strings + escapes so `{` inside a JSON string
    doesn't confuse the depth counter."""
    i = start
    n = len(text)
    while i < n and text[i] != '{' and not text[i].isspace():
        # `##SKILL:name##` or `##SKILL:name` with no args
        break
    # skip whitespace
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != '{':
        return '', i
    depth = 0
    in_str = False
    escape = False
    j = i
    while j < n:
        c = text[j]
        if in_str:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[i:j+1], j + 1
        j += 1
    # unbalanced — give up
    return '', i

class SkillsEngine:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.agent_skills_dir = os.path.join(BASE_DIR, "agents", agent_id, "skills")

    def skill_path(self, skill_name):
        for base in [self.agent_skills_dir, SKILLS_SHARED_DIR]:
            for ext in [".py", ".sh"]:
                path = os.path.join(base, skill_name + ext)
                if os.path.exists(path): return path
        return None

    def run(self, skill_name, args={}, chat_id=None, task_id=None, project_id=None):
        path = self.skill_path(skill_name)
        if not path:
            return {
                "success": False,
                "error": (
                    f"Skill '{skill_name}' not found. "
                    f"Create it dynamically with: "
                    f'##SKILL:create_skill{{"name":"{skill_name}","description":"what it does",'
                    f'"code":"#!/usr/bin/env python3\\nimport os,json\\n'
                    f'args=json.loads(os.environ.get(\'SKILL_ARGS\',\'{{}}\'))\\n'
                    f'print(\'result here\')"}}##'
                )
            }
        start = time.time()
        invoke_event_id = None
        if _task_events is not None:
            invoke_event_id = _task_events.emit(
                "skill_invoked",
                task_id=task_id, project_id=project_id, agent_id=self.agent_id,
                payload={"name": skill_name, "args": args},
            )
        try:
            env = os.environ.copy()
            env["SKILL_ARGS"] = json.dumps(args)
            env["AGENT_ID"] = self.agent_id
            cmd = ["python3", path] if path.endswith(".py") else ["bash", path]
            # Image generation can take several minutes; give it 10 min
            skill_timeout = 600 if any(kw in skill_name for kw in ("image", "generate", "render", "enhance")) else 90
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=skill_timeout, env=env)
            duration_ms = int((time.time()-start)*1000)
            success = proc.returncode == 0
            stdout_clean = (proc.stdout or "").strip()
            stderr_clean = (proc.stderr or "").strip()
            output = stdout_clean if success else (stderr_clean or stdout_clean or f"exit code {proc.returncode}")
            skill_ran(self.agent_id, skill_name)
            journal_log(agent_id=self.agent_id, task_type=f"skill:{skill_name}",
                task_description=f"Ran {skill_name} with {json.dumps(args)}",
                result=output[:500], success=success, input_data=args,
                duration_ms=duration_ms, chat_id=chat_id)
            result = {"success": success, "output": output, "duration_ms": duration_ms, "skill": skill_name}
            if not success:
                # Always populate `error` on failure so callers don't KeyError
                result["error"] = stderr_clean or stdout_clean or f"skill exited with code {proc.returncode}"
                logger.error(f"[skills_engine] {skill_name} failed (exit {proc.returncode}): {result['error'][:300]}")
            if _task_events is not None:
                _task_events.emit(
                    "skill_result",
                    task_id=task_id, project_id=project_id, agent_id=self.agent_id,
                    payload={
                        "name": skill_name, "ok": success,
                        "output_snippet": output[:600], "duration_ms": duration_ms,
                    },
                    parent_event_id=invoke_event_id,
                )
            return result
        except subprocess.TimeoutExpired:
            if _task_events is not None:
                _task_events.emit(
                    "skill_result",
                    task_id=task_id, project_id=project_id, agent_id=self.agent_id,
                    payload={"name": skill_name, "ok": False, "error": "timeout"},
                    parent_event_id=invoke_event_id,
                )
            return {"success": False, "error": f"Skill '{skill_name}' timed out", "output": ""}
        except Exception as e:
            if _task_events is not None:
                _task_events.emit(
                    "skill_result",
                    task_id=task_id, project_id=project_id, agent_id=self.agent_id,
                    payload={"name": skill_name, "ok": False, "error": str(e)[:600]},
                    parent_event_id=invoke_event_id,
                )
            return {"success": False, "error": str(e), "output": ""}

    def parse_and_run(self, llm_output, chat_id=None, task_id=None, project_id=None):
        """Find every ##SKILL:name{...}## call, execute it, and splice the result back into the text.

        Brace-aware: handles nested JSON objects in skill args (e.g. `{"payload": {"k": "v"}}`).
        Error-aware: reports malformed JSON to the agent rather than silently dropping args."""
        results = []
        pieces = []
        pos = 0
        text = llm_output
        for m in SKILL_NAME_PATTERN.finditer(text):
            pieces.append(text[pos:m.start()])
            skill_name = m.group(1)
            json_str, after_json = _extract_json_block(text, m.end())
            # consume trailing "##" terminator if present
            end = after_json
            while end < len(text) and text[end] in ' \t':
                end += 1
            if text[end:end+2] == '##':
                end += 2
            if json_str:
                try:
                    args = json.loads(json_str)
                    parse_error = None
                except json.JSONDecodeError as e:
                    args = {}
                    parse_error = f"malformed JSON in skill args: {e.msg} at pos {e.pos} (raw={json_str[:120]})"
            else:
                args = {}
                parse_error = None
            if parse_error:
                result = {"success": False, "error": parse_error, "skill": skill_name}
                logger.warning(f"[skills_engine] {skill_name}: {parse_error}")
            else:
                result = self.run(skill_name, args, chat_id=chat_id,
                                  task_id=task_id, project_id=project_id)
            results.append(result)
            if result.get("success"):
                pieces.append(f"\n[SKILL RESULT: {skill_name}]\n{result.get('output','')}\n")
            else:
                err_text = result.get('error') or result.get('output') or 'unknown error'
                pieces.append(f"\n[SKILL ERROR: {skill_name}] {err_text}\n")
            pos = end
        pieces.append(text[pos:])
        return ''.join(pieces), results
