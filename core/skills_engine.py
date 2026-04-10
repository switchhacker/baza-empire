import os, re, json, subprocess, logging, time
from typing import Optional
from core.context_db import get_skills, skill_ran, journal_log
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_SHARED_DIR = os.path.join(BASE_DIR, "skills", "shared")
# Accept both ##SKILL: name {args}## (correct) and ##SKILL: name {args} (LLM often forgets closing ##)
SKILL_CALL_PATTERN = re.compile(r'##SKILL:\s*(\w[\w\-]+)\s*({.*?})?\s*(?:##|(?=\n|$))', re.DOTALL)

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

    def run(self, skill_name, args={}, chat_id=None):
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
            return result
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Skill '{skill_name}' timed out", "output": ""}
        except Exception as e:
            return {"success": False, "error": str(e), "output": ""}

    def parse_and_run(self, llm_output, chat_id=None):
        results = []
        modified = llm_output
        for match in SKILL_CALL_PATTERN.finditer(llm_output):
            full_match = match.group(0)
            skill_name = match.group(1)
            try: args = json.loads(match.group(2) or "{}")
            except: args = {}
            result = self.run(skill_name, args, chat_id=chat_id)
            results.append(result)
            if result.get("success"):
                replacement = f"\n[SKILL RESULT: {skill_name}]\n{result.get('output','')}\n"
            else:
                err_text = result.get('error') or result.get('output') or 'unknown error'
                replacement = f"\n[SKILL ERROR: {skill_name}] {err_text}\n"
            modified = modified.replace(full_match, replacement)
        return modified, results
