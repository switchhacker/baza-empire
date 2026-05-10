#!/usr/bin/env python3
"""
Claw Batto — Advanced CLI Coding Agent v2
Cursor/Composer/Copilot-style dev assistant for the Baza Empire terminal.

Features:
  ◆ Full file read/write/diff/patch with git integration
  ◆ Multi-file project context (entire directory trees)
  ◆ Shell command execution with output capture + LLM interpretation
  ◆ Streaming responses with syntax-highlighted code blocks
  ◆ Step-by-step guided builds (Composer mode)
  ◆ Code generation, refactoring, debugging, explanation
  ◆ Auto-detect file paths and stage writes
  ◆ Search codebase (grep-style) with context
  ◆ Service management shortcuts
  ◆ Session save/load
  ◆ Powered by local Ollama — zero cloud

Usage:
  claw                              Interactive REPL
  claw "build me a flask API"       One-shot
  claw -f app.py "fix this"         With file context
  claw -d ./agents/simon_bately/    Whole directory context
  claw --composer "new feature"     Step-by-step build mode
  claw --model qwen2.5:14b          Use different model
"""

import os, sys, json, re, subprocess, difflib, shutil, hashlib, ast
import urllib.request, urllib.error, readline, signal, textwrap, tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
CLAW_VERSION  = "2.0.0"
OLLAMA_HOST   = os.environ.get("OLLAMA_HOST",   "http://localhost:11434")
CLAW_MODEL    = os.environ.get("CLAW_MODEL",    "mistral-small:22b")
CONTEXT_LIMIT = int(os.environ.get("CLAW_CTX",  "8192"))
MAX_FILE_KB   = int(os.environ.get("CLAW_MAXKB","64"))
SESSION_DIR   = os.path.expanduser("~/.claw_sessions")
_CLAW_STOP    = False  # set True to interrupt streaming
FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLAW_SYSTEM = f"""You are Claw Batto — Senior Developer and DevOps Engineer for Baza Empire.
You run as an advanced CLI coding assistant directly on the baza server.
Framework root: {FRAMEWORK_DIR}
Server: baza (Ryzen 7 5700G, RTX 3070 CUDA + RX 6700 XT Vulkan, 64GB RAM, Ubuntu 24.04 LTS)
Stack: Python, Bash, Flask, SQLite/PostgreSQL, Ollama (AMD:11434 / NV:11435), systemd, nginx
Venv: {FRAMEWORK_DIR}/venv/bin/python — always use this, never system python
Dashboard: http://localhost:8888 | SD WebUI: http://localhost:7860

YOUR JOB:
- Pair-program with Serge at senior engineer level — no hand-holding, no dumbing down
- Generate complete, production-ready code — never stubs or placeholders
- Guide step-by-step through complex builds
- Debug by reading actual error output — find root cause before touching anything
- Refactor code to be clean, minimal, and efficient

WHEN WRITING CODE:
- Complete files only — all imports, error handling, edge cases
- Python: PEP8, type hints on function signatures, f-strings, explicit error handling
- Bash: set -euo pipefail, quote all variables, comment non-obvious lines
- Always include a test or validation command
- Prefer existing Baza stack patterns over introducing new dependencies
- Never use system pip — always {FRAMEWORK_DIR}/venv/bin/pip

WHEN SUGGESTING FILE WRITES:
- Format: <<<WRITE: /absolute/path/to/file.py>>>
- Follow immediately with the complete file content in a fenced code block
- Show diff summary: what changed and why

WHEN SUGGESTING SHELL COMMANDS:
- Format: <<<RUN: brief description>>>
- Follow with command(s) in a code block
- Flag destructive operations with ⚠

AGENTIC PROPOSAL MODE:
When you have a clear multi-step plan, present it as:
<<<PLAN: short title>>>
STEP 1: [description] → TYPE: WRITE|RUN|PIP|APT|MKDIR|SYMLINK
  path or command...
STEP 2: ...
<<<END_PLAN>>>
User says OK/go/yes/proceed to execute. Say "auto" for hands-free execution.

DYNAMIC SKILL CREATION:
If an agent needs a tool, create it on the fly. Skills are Python scripts in skills/shared/.
Structure: read args from SKILL_ARGS env var, print result to stdout, exit 0 on success.

AVAILABLE CLI COMMANDS (tell Serge about these when relevant):
/agent <id> <task>    — dispatch task to another Baza agent via Telegram
/skill list|run|create — manage skills from CLI
/tasks [status]       — view project tasks from SQLite DB
/memory [key] [val]   — view/set Claw's persistent memory
/db <query>           — run SQLite query on baza_projects.db
/commit [msg]         — AI-assisted git commit
/review [file]        — AI code review
/summarize            — summarize conversation to memory

PERSONALITY: Terse, technical, zero filler. Senior engineer energy.
Concrete answers. If not sure, say so and tell Serge how to verify.
"""

# ── ANSI Colors — dark navy + yellow accent theme ─────────────────────────────
IS_TTY = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if IS_TTY else text

def _c2(fg: str, bg: str, text: str) -> str:
    return f"\033[{fg};{bg}m{text}\033[0m" if IS_TTY else text

def bold(t):       return _c("1", t)
def dim(t):        return _c("2", t)
def italic(t):     return _c("3", t)
def red(t):        return _c("31", t)
def green(t):      return _c("32", t)
def yellow(t):     return _c("33", t)
def blue(t):       return _c("34", t)
def magenta(t):    return _c("35", t)
def cyan(t):       return _c("38;5;44", t)   # aqua / bright teal
def white(t):      return _c("97", t)
# Welcome-box only colors (not used in UI chrome)
def crimson(t):    return _c("38;5;124", t)    # muted dark red — logo/box
def scarlet(t):    return _c("38;5;160", t)    # medium red — v2 tag
def deep_blue(t):  return _c("38;5;18", t)     # deep navy — box text
def steel(t):      return _c("38;5;67", t)     # steel blue — box labels
def accent(t):     return bold(cyan(t))

def divider(char="─", width=70, color=cyan):
    return color(char * width)

def info(msg):    print(dim(f"  ℹ  {msg}"))
def ok(msg):      print(green(f"  ✅  {msg}"))
def warn(msg):    print(yellow(f"  ⚠   {msg}"))
def error(msg):   print(red(f"  ✗   {msg}"), file=sys.stderr)
def step(n, msg): print(cyan(f"\n  [{n}] ") + white(msg))

# ── Syntax highlighting (minimal, terminal-safe) ───────────────────────────────
LANG_COLORS = {
    "python": "33", "py": "33",
    "bash": "32", "sh": "32", "shell": "32",
    "json": "36",
    "yaml": "35", "yml": "35",
    "html": "34",
    "javascript": "33", "js": "33", "typescript": "33", "ts": "33",
    "sql": "36",
    "text": "37", "": "37",
}

def print_code_block(lang: str, code: str):
    color_code = LANG_COLORS.get(lang.lower(), "37")
    header     = bold(cyan(f"  ┌─ {lang or 'code'} {'─'*(max(0, 60-len(lang)))}"))
    print(header)
    for line in code.split("\n"):
        print(f"  │ " + _c(color_code, line))
    print(cyan("  └" + "─" * 65))

# ── Ollama client ─────────────────────────────────────────────────────────────
def ollama_available() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3)
        return True
    except:
        return False

def ollama_models() -> list:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except:
        return []

def ollama_chat(messages: list, system: str = "", stream: bool = True,
                model: str = "") -> str:
    global _CLAW_STOP
    model = model or CLAW_MODEL
    system = system or CLAW_SYSTEM

    # Truncate context if too large to avoid runaway generation
    serialized = json.dumps(messages)
    if len(serialized) > 24000:
        warn("Input large — truncating to last 8 messages to prevent runaway output")
        messages = messages[-8:]

    payload = json.dumps({
        "model":    model,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream":   stream,
        "options":  {
            "num_ctx":     CONTEXT_LIMIT,
            "temperature": 0.15,
            "num_predict": 2048,   # hard cap: max 2048 tokens per response
        },
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    full = []
    char_count = 0
    CHAR_LIMIT = 6000  # stop printing after ~6000 chars and warn

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            if not stream:
                data = json.loads(resp.read())
                return data.get("message", {}).get("content", "")

            print(f"\n{cyan('Claw')} {dim('›')} ", end="", flush=True)
            _CLAW_STOP = False
            for raw_line in resp:
                if _CLAW_STOP:
                    print(f"\n{yellow('  [Stopped by user — Ctrl+C]')} ")
                    break
                try:
                    chunk = json.loads(raw_line.decode())
                except:
                    continue
                token = chunk.get("message", {}).get("content", "")
                if token:
                    full.append(token)
                    char_count += len(token)
                    print(token, end="", flush=True)
                    if char_count >= CHAR_LIMIT:
                        print(f"\n{yellow('  [Response limit reached — use /continue for more]')} ")
                        break
                if chunk.get("done"):
                    break
            print()

    except urllib.error.URLError as e:
        error(f"Ollama error: {e}")
        error(f"Is Ollama running at {OLLAMA_HOST}?")
        return ""
    except KeyboardInterrupt:
        print(f"\n{dim('  [Interrupted]')} ")
        return "".join(full)

    return "".join(full)

# ── File context manager ───────────────────────────────────────────────────────
class ClawContext:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.pending: dict[str, str] = {}

    def load_file(self, path: str) -> bool:
        p = Path(path).expanduser()
        if not p.exists():
            error(f"Not found: {path}")
            return False
        if p.is_dir():
            return self.load_dir(str(p))
        if p.stat().st_size > MAX_FILE_KB * 1024:
            warn(f"{p.name} is large ({p.stat().st_size//1024}KB) — loading first {MAX_FILE_KB}KB")
        try:
            content = p.read_text(errors="ignore")[:MAX_FILE_KB*1024]
            self.files[str(p.resolve())] = content
            ok(f"Loaded: {p} ({len(content.splitlines())} lines)")
            return True
        except Exception as e:
            error(f"Cannot read {path}: {e}")
            return False

    def load_dir(self, path: str, extensions: Optional[list] = None, max_files: int = 20) -> bool:
        p = Path(path).expanduser()
        if not p.is_dir():
            error(f"Not a directory: {path}")
            return False
        exts = set(extensions or [".py",".sh",".yaml",".yml",".json",".md",".ts",".js",".html",".conf",".toml"])
        loaded = 0
        skipped = []
        for f in sorted(p.rglob("*")):
            if not f.is_file(): continue
            if f.suffix not in exts: continue
            if any(part in str(f) for part in ["venv/","__pycache__/",".git/","node_modules/"]): continue
            if loaded >= max_files:
                skipped.append(f.name)
                continue
            try:
                content = f.read_text(errors="ignore")[:MAX_FILE_KB*1024]
                self.files[str(f.resolve())] = content
                loaded += 1
            except:
                pass
        ok(f"Loaded {loaded} files from {p}")
        if skipped:
            warn(f"Skipped {len(skipped)} files (limit {max_files}). Use /file to add specific ones.")
        return loaded > 0

    def get_context(self) -> str:
        if not self.files:
            return ""
        parts = []
        for path, content in self.files.items():
            lines = len(content.splitlines())
            parts.append(f"=== FILE: {path} ({lines} lines) ===\n{content}\n=== END ===")
        return "\n\n".join(parts)

    def stage(self, path: str, content: str):
        abs_path = str(Path(path).expanduser().resolve())
        self.pending[abs_path] = content
        warn(f"Staged for write: {abs_path}")
        info("Review with /diff then /apply to write to disk")

    def diff(self, path: Optional[str] = None) -> str:
        targets = [path] if path else list(self.pending.keys())
        out = []
        for p in targets:
            if p not in self.pending:
                continue
            existing = Path(p).read_text(errors="ignore") if Path(p).exists() else ""
            diff_lines = list(difflib.unified_diff(
                existing.splitlines(keepends=True),
                self.pending[p].splitlines(keepends=True),
                fromfile=f"a/{Path(p).name}",
                tofile=f"b/{Path(p).name}",
                lineterm=""
            ))
            if diff_lines:
                out.append(f"\n{'─'*60}\n{p}\n{'─'*60}")
                for line in diff_lines:
                    if line.startswith("+"): out.append(green(line))
                    elif line.startswith("-"): out.append(red(line))
                    elif line.startswith("@"): out.append(cyan(line))
                    else: out.append(line)
            else:
                out.append(f"(no changes: {p})")
        return "\n".join(out) if out else "(no pending changes)"

    def apply(self, path: Optional[str] = None) -> list:
        targets = [path] if path else list(self.pending.keys())
        written = []
        for p in targets:
            if p not in self.pending:
                continue
            dest = Path(p)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                bak = dest.with_suffix(dest.suffix + ".bak")
                shutil.copy2(dest, bak)
                info(f"Backup → {bak}")
            dest.write_text(self.pending[p])
            self.files[p] = self.pending[p]
            del self.pending[p]
            written.append(p)
            ok(f"Written: {dest}")
        return written

    def search(self, pattern: str, path: Optional[str] = None) -> list:
        """Grep loaded files for pattern."""
        results = []
        targets = {path: self.files[path]} if path and path in self.files else self.files
        for fpath, content in targets.items():
            for i, line in enumerate(content.splitlines(), 1):
                if re.search(pattern, line, re.IGNORECASE):
                    results.append({"file": fpath, "line": i, "content": line.strip()})
        return results

    def show(self):
        print(divider())
        if self.files:
            print(bold(cyan("  Loaded files:")))
            for p, content in self.files.items():
                sz = len(content.splitlines())
                print(f"    {dim(p)} ({sz} lines)")
        else:
            print(dim("  No files loaded"))
        if self.pending:
            print(bold(yellow("\n  Staged (not yet written):")))
            for p in self.pending:
                sz = len(self.pending[p].splitlines())
                print(f"    {yellow(p)} ({sz} lines)")
        print(divider())

# ── Shell runner ───────────────────────────────────────────────────────────────
def confirm_action(prompt: str) -> bool:
    """Ask user to confirm a destructive action."""
    try:
        ans = input(f"  {yellow('⚠')}  {prompt} [y/N] ").strip().lower()
        return ans == 'y'
    except (EOFError, KeyboardInterrupt):
        return False

def run_shell(cmd: str, cwd: str = None, capture: bool = True) -> tuple[int, str]:
    print(f"\n{bold(dim('  $'))} {cyan(cmd)}")
    try:
        if capture:
            result = subprocess.run(cmd, shell=True, capture_output=True,
                                    text=True, timeout=60, cwd=cwd)
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                print(dim(textwrap.indent(output[:3000], "    ")))
            else:
                print(red(textwrap.indent(output[:3000], "    ")))
            return result.returncode, output
        else:
            result = subprocess.run(cmd, shell=True, timeout=60, cwd=cwd)
            return result.returncode, ""
    except subprocess.TimeoutExpired:
        error("Command timed out (60s)")
        return -1, "timeout"
    except Exception as e:
        error(f"Shell error: {e}")
        return -1, str(e)

def service_status(name: str) -> str:
    rc, out = run_shell(f"systemctl is-active {name}", capture=True)
    return "active" if out.strip() == "active" else out.strip()

# ── Code extraction ────────────────────────────────────────────────────────────
def extract_code_blocks(text: str) -> list[dict]:
    return [{"lang": m.group(1) or "text", "content": m.group(2).strip()}
            for m in re.finditer(r'```(\w+)?\n(.*?)```', text, re.DOTALL)]

def extract_write_targets(text: str) -> list[dict]:
    """Detect <<<WRITE: path>>> markers from Claw's response."""
    results = []
    pattern = re.compile(r'<<<WRITE:\s*([^\n>]+)>>>\s*```(?:\w+)?\n(.*?)```', re.DOTALL)
    for m in pattern.finditer(text):
        results.append({"path": m.group(1).strip(), "content": m.group(2).strip()})
    return results

# ── Composer mode ──────────────────────────────────────────────────────────────
def composer_mode(task: str, session: 'Session'):
    """Step-by-step guided build — like Cursor composer."""
    print(f"\n{bold(magenta('  ◆ COMPOSER MODE'))}")
    print(f"  {dim('Task:')} {white(task)}")
    print(divider())

    # Step 1: Plan
    step(1, "Planning...")
    plan_msg = [{"role": "user", "content":
        f"I need to build: {task}\n\n"
        f"Break this into 3-6 concrete implementation steps. "
        f"For each step: what file to create/modify, what the code should do, "
        f"and what command to run to test it. Be specific and actionable. "
        f"Format as numbered steps. No code yet — just the plan."}]
    plan = ollama_chat(plan_msg)
    if not plan: return
    session.add("user", plan_msg[0]["content"])
    session.add("assistant", plan)

    # Parse steps
    steps = re.findall(r'^\s*\d+[\.\)]\s+(.+?)(?=^\s*\d+[\.\)]|\Z)', plan, re.MULTILINE | re.DOTALL)
    if not steps:
        steps = [plan]

    print(f"\n{bold(cyan('  Proceed with implementation? [Y/n] '))}", end="")
    if input().strip().lower() == 'n':
        return

    # Step 2+: Implement each step
    for i, step_desc in enumerate(steps, 2):
        step_desc = step_desc.strip()
        if not step_desc: continue
        step(i, f"Implementing: {step_desc[:80]}")

        ctx = session.ctx.get_context()
        msgs = session.messages + [{"role": "user", "content":
            f"Implement step: {step_desc}\n\n"
            + (f"Current context:\n{ctx}\n\n" if ctx else "")
            + "Produce complete, working code. Use <<<WRITE: /path/file.ext>>> markers for files to create."}]

        response = ollama_chat(msgs)
        if not response: continue
        session.add("user", msgs[-1]["content"])
        session.add("assistant", response)

        # Auto-stage file writes
        writes = extract_write_targets(response)
        for w in writes:
            wpath = w["path"]; confirm = input(f"\n  Stage {wpath}? [Y/n] ").strip().lower()
            if confirm != 'n':
                session.ctx.stage(w["path"], w["content"])

        # Extract any commands to run
        cmds = re.findall(r'`([^`]{5,200})`', response)
        shell_cmds = [c for c in cmds if any(c.strip().startswith(k)
                       for k in ["sudo","systemctl","pip","python","bash","git","apt","curl"])]
        if shell_cmds:
            for cmd in shell_cmds[:2]:
                run_q = input(f"\n{cyan(f'  Run: {cmd[:80]}?')} [y/N] ").strip().lower()
                if run_q == 'y':
                    run_shell(cmd, cwd=session.cwd)

    # Final: apply and test
    if session.ctx.pending:
        print(f"\n{bold('  All steps complete. Review diffs:')}")
        print(session.ctx.diff())
        apply_q = input(f"\n{cyan('  Apply all changes?')} [Y/n] ").strip().lower()
        if apply_q != 'n':
            session.ctx.apply()

# ── Session ────────────────────────────────────────────────────────────────────
class Session:
    def __init__(self):
        self.messages: list[dict] = []
        self.ctx = ClawContext()
        self.cwd = FRAMEWORK_DIR
        self.last_code = ""
        self.last_response = ""
        self.start_time = datetime.now()
        self.name = ""
        self.pending_proposal = None
        self.auto_approve = False  # set True when user picks "Always run"

    def add(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > 50:
            # Keep system-level messages + last 40
            self.messages = self.messages[-40:]

    def build_user_msg(self, text: str) -> str:
        ctx = self.ctx.get_context()
        if ctx:
            return f"{ctx}\n\n{text}"
        return text

    def save(self, name: str = ""):
        os.makedirs(SESSION_DIR, exist_ok=True)
        name = name or datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(SESSION_DIR, f"{name}.json")
        data = {
            "name": name,
            "messages": self.messages,
            "cwd": self.cwd,
            "files": list(self.ctx.files.keys()),
            "saved_at": datetime.now().isoformat(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        ok(f"Session saved: {path}")

    def load(self, name_or_path: str):
        path = name_or_path
        if not os.path.exists(path):
            path = os.path.join(SESSION_DIR, f"{name_or_path}.json")
        if not os.path.exists(path):
            error(f"Session not found: {name_or_path}")
            return
        with open(path) as f:
            data = json.load(f)
        self.messages = data.get("messages", [])
        self.cwd      = data.get("cwd", FRAMEWORK_DIR)
        ok(f"Session loaded: {data.get('name','?')} ({len(self.messages)} messages)")
        # Re-load files
        for fpath in data.get("files", []):
            self.ctx.load_file(fpath)

# ── Action dialogue box ────────────────────────────────────────────────────────
def ask_action(title: str, items: list, auto: bool = False) -> str:
    """
    Show a styled proposal dialogue box and return the user's choice.
    Returns: 'y' (yes, run once) | 'a' (always, auto future) | 'n' (no/skip)
    If auto=True, skips prompt and returns 'a'.
    """
    if auto:
        return 'a'

    BOX_W = 64

    def brow(content: str = "") -> str:
        bare = len(_bare(content))
        pad  = max(0, BOX_W - bare)
        return cyan("  │") + content + " " * pad + cyan("│")

    print()
    print(cyan("  ╭" + "─" * BOX_W + "╮"))
    print(brow(f"  {bold(white('◆'))}  {bold(cyan(title))}"))
    print(brow())

    for item in items:
        bare_item = _bare(item)
        # Main line
        print(brow(f"  {item}"))
        # Truncated target path on next line if it's long
        if len(bare_item) > BOX_W - 4:
            print(brow(f"    {dim(bare_item[-(BOX_W-8):])}"))

    print(brow())

    # Options line
    yes_str   = green("  [Y]") + white(" Yes, run")
    always_str = yellow("  [A]") + white(" Yes, always run")
    no_str    = red("  [N]") + white(" No")
    opts_bare = "  [Y] Yes, run  [A] Yes, always run  [N] No"
    opts_pad  = max(0, BOX_W - len(opts_bare))
    print(cyan("  │") + yes_str + always_str + no_str + " " * opts_pad + cyan("│"))
    print(cyan("  ╰" + "─" * BOX_W + "╯"))

    while True:
        try:
            ans = input(f"\n  {cyan('›')} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 'n'
        if ans in ('y', 'yes', 'ok', 'go', 'proceed', 'run', ''):
            return 'y'
        if ans in ('a', 'always', 'auto', 'aa'):
            return 'a'
        if ans in ('n', 'no', 'cancel', 'skip', 'abort'):
            return 'n'
        print(f"  {dim('Type Y, A, or N')}")


def show_proposal_box(proposal: 'Proposal', session: 'Session') -> str:
    """
    Display a proposal in the ask_action box.
    Executes immediately — returns 'y', 'a', or 'n'.
    """
    items = []
    for s in proposal.steps:
        icon = cyan("✏") if s.kind == "WRITE" else yellow("⚡") if s.kind == "RUN" else cyan("◆")
        kind_label = dim(f"[{s.kind}]")
        target_short = s.target if len(s.target) <= 52 else "…" + s.target[-50:]
        items.append(f"{icon} {kind_label} {white(s.desc)}")
        items.append(f"     {dim('→')} {dim(target_short)}")

    return ask_action(f"Claw suggests {len(proposal.steps)} action(s)", items,
                      auto=session.auto_approve)


# ── Proposal / plan execution system ──────────────────────────────────────────
from typing import NamedTuple

class ProposalStep(NamedTuple):
    num:     int
    desc:    str
    kind:    str   # WRITE | RUN | PIP | APT | MKDIR | SYMLINK
    target:  str   # file path or shell command
    content: str   # file content for WRITE steps

class Proposal(NamedTuple):
    title: str
    steps: list    # list[ProposalStep]

def is_approval(text: str) -> bool:
    return text.strip().lower() in (
        "ok", "go", "yes", "y", "proceed", "run", "do it", "auto", "sure", "yep"
    )

def parse_plan_block(text: str) -> Optional['Proposal']:
    """Parse <<<PLAN: title>>> … <<<END_PLAN>>> from LLM response."""
    m = re.search(r'<<<PLAN:\s*([^\n>]+)>>>(.*?)<<<END_PLAN>>>',
                  text, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    title = m.group(1).strip()
    body  = m.group(2).strip()
    steps: list[ProposalStep] = []
    pat = re.compile(
        r'STEP\s+(\d+):\s+(.+?)\s+→\s+TYPE:\s+(\w+)\s*\n\s*(.+?)(?=STEP\s+\d+:|$)',
        re.DOTALL | re.IGNORECASE
    )
    for sm in pat.finditer(body):
        num    = int(sm.group(1))
        desc   = sm.group(2).strip()
        kind   = sm.group(3).upper()
        target = sm.group(4).strip().splitlines()[0].strip()
        steps.append(ProposalStep(num, desc, kind, target, ""))
    return Proposal(title, steps) if steps else None

def parse_write_targets_from_response(text: str) -> list:
    """Find <<<WRITE: path>>> code-block pairs."""
    pat = re.compile(r'<<<WRITE:\s*([^\n>]+)>>>\s*```(?:\w+)?\n(.*?)```', re.DOTALL)
    return [{"path": m.group(1).strip(), "content": m.group(2).strip()}
            for m in pat.finditer(text)]

def parse_run_targets_from_response(text: str) -> list:
    """Find <<<RUN: desc>>> code-block pairs."""
    pat = re.compile(r'<<<RUN:\s*([^\n>]+)>>>\s*```(?:\w+)?\n(.*?)```', re.DOTALL)
    return [{"desc": m.group(1).strip(),
             "cmd":  m.group(2).strip().splitlines()[0]}
            for m in pat.finditer(text)]

def run_proposal(proposal: 'Proposal', session: 'Session', auto: bool = False):
    """Execute a structured plan step-by-step with per-step dialogues."""
    if auto:
        session.auto_approve = True

    print(f"\n  {bold(cyan('◆'))} {bold(white(proposal.title))}")
    print(cyan("  " + "─" * 50))

    for s in proposal.steps:
        step(s.num, s.desc)

        if s.kind == "WRITE":
            if not s.content:
                warn(f"No content for WRITE step: {s.target}"); continue
            # Show a mini dialogue for each write
            ans = ask_action(
                f"Write file",
                [f"{cyan('✏')} {dim('[WRITE]')} {white(Path(s.target).name)}",
                 f"     {dim('→')} {dim(s.target)}"],
                auto=session.auto_approve
            )
            if ans == 'n':
                info("Skipped."); continue
            if ans == 'a':
                session.auto_approve = True
            session.ctx.stage(s.target, s.content)
            if session.ctx.apply(s.target):
                ok(f"Written: {s.target}")

        elif s.kind == "RUN":
            ans = ask_action(
                f"Run command",
                [f"{yellow('⚡')} {dim('[RUN]')}  {white(s.target[:60])}"],
                auto=session.auto_approve
            )
            if ans == 'n':
                info("Skipped."); continue
            if ans == 'a':
                session.auto_approve = True
            run_shell(s.target, cwd=session.cwd)

        elif s.kind == "PIP":
            venv_pip = os.path.join(FRAMEWORK_DIR, "venv", "bin", "pip")
            pip_bin  = venv_pip if os.path.exists(venv_pip) else "pip3"
            ans = ask_action("Install package",
                             [f"{cyan('◆')} {dim('[PIP]')} {white(s.target)}"],
                             auto=session.auto_approve)
            if ans == 'n': info("Skipped."); continue
            if ans == 'a': session.auto_approve = True
            run_shell(f"{pip_bin} install {s.target}", cwd=session.cwd)

        elif s.kind == "APT":
            ans = ask_action("Install apt package",
                             [f"{cyan('◆')} {dim('[APT]')} {white(s.target)}"],
                             auto=session.auto_approve)
            if ans == 'n': info("Skipped."); continue
            if ans == 'a': session.auto_approve = True
            run_shell(f"sudo apt install -y {s.target}", cwd=session.cwd)

        elif s.kind == "MKDIR":
            Path(s.target).mkdir(parents=True, exist_ok=True)
            ok(f"Created: {s.target}")

        elif s.kind == "SYMLINK":
            pts = s.target.split(None, 1)
            if len(pts) == 2:
                run_shell(f"ln -sf {pts[0]} {pts[1]}", cwd=session.cwd)

    print(f"\n  {green('✓ Done.')}\n")

def run_doctor():
    """Health check: Ollama, services, GPU."""
    print(f"\n  {bold(yellow('  ◆ Claw Doctor — System Health Check'))}")
    print(yellow("  " + "─" * 48))
    for host, label in [(OLLAMA_HOST, "Ollama AMD/primary"),
                        ("http://localhost:11435", "Ollama NVIDIA")]:
        try:
            urllib.request.urlopen(f"{host}/api/tags", timeout=3)
            print(f"  {green('●')} {label:<24} {green('online')}  {dim(host)}")
        except Exception:
            print(f"  {red('●')} {label:<24} {red('offline')} {dim(host)}")
    print(yellow("  " + "─" * 48))
    for svc, label in [("baza-dashboard",    "Dashboard"),
                       ("baza-agents",        "Agents"),
                       ("baza-task-runner",   "Task runner"),
                       ("baza-tool-server",   "Tool server"),
                       ("postgresql",         "PostgreSQL"),
                       ("redis",              "Redis")]:
        _, stat = run_shell(f"systemctl is-active {svc} 2>/dev/null", capture=True)
        st  = stat.strip()
        col = green if st == "active" else yellow if st == "activating" else red
        print(f"  {col('●')} {label:<24} {dim(st)}")
    print(yellow("  " + "─" * 48))
    run_shell("nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu "
              "--format=csv,noheader 2>/dev/null || echo 'nvidia-smi unavailable'")
    models = ollama_models()
    if models:
        print(f"\n  {bold(yellow('Ollama models:'))}")
        for m in models:
            marker = f"  {yellow('◆')}" if m == CLAW_MODEL else "   "
            print(f"{marker} {m}")
    print()

def watch_mode(filepath: str, session: 'Session', prompt: str = ""):
    """Watch a file and auto-review on every save (1-second poll)."""
    import time as _time
    p = Path(filepath).expanduser()
    if not p.exists():
        error(f"File not found: {filepath}"); return
    session.ctx.load_file(str(p))
    review_prompt = prompt or "Review this file for bugs, issues, and improvements."
    info(f"Watching: {p}  (Ctrl+C to stop)")
    last_mtime = p.stat().st_mtime
    try:
        while True:
            _time.sleep(1)
            try:
                mtime = p.stat().st_mtime
            except FileNotFoundError:
                warn("File deleted — stopping watch."); break
            if mtime != last_mtime:
                last_mtime = mtime
                print(f"\n  {yellow('◆ Changed:')} {p.name}")
                content = p.read_text(errors="ignore")[:MAX_FILE_KB * 1024]
                session.ctx.files[str(p.resolve())] = content
                msgs = [{"role": "user", "content":
                    f"{review_prompt}\n\nFile: {p}\n\n```\n{content[:5000]}\n```"}]
                resp = ollama_chat(msgs)
                if resp:
                    session.last_response = resp
    except KeyboardInterrupt:
        print(f"\n{dim('  Watch stopped.')}")


# ── Slash commands ─────────────────────────────────────────────────────────────
def handle_slash(cmd: str, session: Session) -> bool:
    global CLAW_MODEL
    parts = cmd.strip().split(None, 1)
    name  = parts[0].lower()
    arg   = parts[1].strip() if len(parts) > 1 else ""

    if name in ("/f", "/file"):
        if arg: session.ctx.load_file(arg)
        else: warn("Usage: /file <path>")

    elif name in ("/d", "/dir"):
        if arg: session.ctx.load_dir(arg)
        else: session.ctx.load_dir(session.cwd)

    elif name in ("/r", "/run"):
        if arg: run_shell(arg, cwd=session.cwd)
        else: warn("Usage: /run <command>")

    elif name == "/write":
        if not session.last_code:
            warn("No code captured yet")
        elif not arg:
            warn("Usage: /write <path>")
        else:
            session.ctx.stage(arg, session.last_code)

    elif name in ("/diff", "/d"):
        print(session.ctx.diff(arg if arg else None))

    elif name in ("/apply", "/a"):
        written = session.ctx.apply(arg if arg else None)
        if written:
            info("Restart affected services? Use /svc restart <name>")

    elif name == "/search":
        if not arg: warn("Usage: /search <pattern> [file]"); return True
        parts2 = arg.split(None, 1)
        pattern = parts2[0]
        target  = parts2[1] if len(parts2) > 1 else None
        results = session.ctx.search(pattern, target)
        if results:
            for r in results[:20]:
                print(f"  {dim(r['file'])}:{cyan(str(r['line']))} {r['content'][:120]}")
        else:
            info("No matches found")

    elif name == "/grep":
        if not arg: warn("Usage: /grep <pattern> [path]"); return True
        parts2 = arg.split(None, 1)
        pattern = parts2[0]
        path    = parts2[1] if len(parts2) > 1 else session.cwd
        run_shell(f"grep -rn '{pattern}' {path} --include='*.py' --include='*.sh' --include='*.yaml' 2>/dev/null | head -30",
                  cwd=session.cwd)

    elif name == "/context":
        session.ctx.show()
        print(f"  {dim('Messages:')} {len(session.messages)}")
        print(f"  {dim('CWD:')} {session.cwd}")
        print(f"  {dim('Model:')} {CLAW_MODEL}")

    elif name == "/clear":
        had_files   = len(session.ctx.files)
        had_msgs    = len(session.messages)
        had_pending = len(session.ctx.pending)

        session.ctx.files.clear()
        session.ctx.pending.clear()
        session.messages.clear()
        session.last_code        = ""
        session.last_response    = ""
        session.pending_proposal = None

        print(f"\n  {bold(yellow('━' * 46))}")
        print(f"  {bold(yellow('  ⟳  Session Reset'))}")
        print(f"  {yellow('━' * 46)}")
        if had_files:
            print(f"  {dim('·')} {dim(str(had_files) + ' file(s) unloaded')}")
        if had_msgs:
            print(f"  {dim('·')} {dim(str(had_msgs) + ' message(s) cleared')}")
        if had_pending:
            print(f"  {dim('·')} {red(str(had_pending) + ' staged file(s) discarded')}")
        print(f"  {steel('Ready.')}  {dim('Load a file with')} {yellow('/file')} {dim('or ask anything.')}")
        print(f"  {yellow('━' * 46)}\n")

    elif name == "/cd":
        if arg and os.path.isdir(os.path.expanduser(arg)):
            session.cwd = os.path.realpath(os.path.expanduser(arg))
            ok(f"CWD: {session.cwd}")
        else:
            error(f"Not a directory: {arg}")

    elif name == "/ls":
        target = arg or session.cwd
        run_shell(f"ls -la {target}", cwd=session.cwd)

    elif name == "/cat":
        if arg:
            p = Path(arg).expanduser()
            if p.exists():
                lang = p.suffix.lstrip(".")
                print_code_block(lang, p.read_text(errors="ignore")[:4000])
            else: error(f"File not found: {arg}")
        else: warn("Usage: /cat <file>")

    elif name == "/svc":
        parts2 = arg.split(None, 1) if arg else []
        action = parts2[0] if parts2 else "status"
        svc    = parts2[1] if len(parts2) > 1 else ""
        if action in ("restart","stop","start","status","logs"):
            if not svc: warn(f"Usage: /svc {action} <service-name>"); return True
            if action == "logs":
                run_shell(f"journalctl -u {svc} -n 30 --no-pager", cwd=session.cwd)
            else:
                run_shell(f"sudo systemctl {action} {svc}", cwd=session.cwd)
        else:
            run_shell(f"systemctl list-units 'baza-*' --no-pager", cwd=session.cwd)

    elif name == "/git":
        run_shell(f"git {arg}" if arg else "git status", cwd=session.cwd)

    elif name == "/models":
        models = ollama_models()
        print(cyan("\n  Available Ollama models:"))
        for m in models:
            marker = "  ◆ " if m == CLAW_MODEL else "    "
            print(f"{marker}{m}")
        print()

    elif name == "/model":
        if arg:
            CLAW_MODEL = arg
            ok(f"Model: {CLAW_MODEL}")
        else:
            info(f"Current model: {CLAW_MODEL}")

    elif name == "/doctor":
        run_doctor()

    elif name == "/watch":
        if not arg:
            warn("Usage: /watch <file_path>  [optional prompt]")
        else:
            parts = arg.split(None, 1)
            fpath = parts[0]
            wprompt = parts[1] if len(parts) > 1 else ""
            watch_mode(fpath, session, prompt=wprompt)

    elif name in ("/stop", "/s"):
        global _CLAW_STOP
        _CLAW_STOP = True
        ok("Stopped. Type your next message or /clear to reset context.")

    elif name in ("/continue", "/c"):
        _CLAW_STOP = False
        if session.messages:
            session.messages.append({"role": "user", "content": "continue"})
            resp = ollama_chat(session.messages)
            if resp:
                session.last_response = resp
                session.add("assistant", resp)
        else:
            warn("Nothing to continue.")

    elif name == "/save":
        session.save(arg)

    elif name == "/load":
        session.load(arg)

    elif name == "/sessions":
        if os.path.exists(SESSION_DIR):
            files = sorted(Path(SESSION_DIR).glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[:10]:
                print(f"  {f.stem}")
        else:
            info("No saved sessions")

    elif name == "/composer":
        if arg:
            composer_mode(arg, session)
        else:
            warn("Usage: /composer <task description>")

    elif name in ("/explain", "/why"):
        if session.last_response:
            msgs = session.messages + [{"role": "user", "content":
                "Explain the last response in simpler terms. What does this code do and why?"}]
            resp = ollama_chat(msgs)
            if resp: session.add("assistant", resp)
        else:
            warn("No previous response to explain")

    elif name == "/fix":
        if not arg: warn("Usage: /fix <error message or description>"); return True
        ctx = session.ctx.get_context()
        msgs = session.messages + [{"role": "user", "content":
            f"Fix this error/issue:\n{arg}\n"
            + (f"\nContext:\n{ctx}" if ctx else "")}]
        resp = ollama_chat(msgs)
        if resp:
            session.add("user", msgs[-1]["content"])
            session.add("assistant", resp)
            blocks = extract_code_blocks(resp)
            if blocks: session.last_code = blocks[-1]["content"]

    elif name == "/test":
        if arg:
            run_shell(f"cd {session.cwd} && python -m pytest {arg} -v 2>&1 | head -60")
        else:
            run_shell(f"cd {session.cwd} && python -m pytest -v 2>&1 | head -60")

    elif name == "/infra":
        run_shell("systemctl list-units 'baza-*' --no-pager")
        run_shell("nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null || echo 'nvidia-smi unavailable'")
        run_shell("free -h | head -2")
        run_shell("df -h / | tail -1")


    elif name in ("/mkdir", "/md"):
        if not arg:
            warn("Usage: /mkdir <path> [path2 ...]"); return True
        for d in arg.split():
            p = Path(d).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            ok(f"Created: {p}")

    elif name in ("/cp", "/copy"):
        parts2 = arg.split()
        if len(parts2) < 2:
            warn("Usage: /cp <src> <dst>"); return True
        src, dst = Path(parts2[0]).expanduser(), Path(parts2[1]).expanduser()
        if not src.exists():
            error(f"Source not found: {src}"); return True
        import shutil as _sh
        if src.is_dir():
            _sh.copytree(src, dst, dirs_exist_ok=True)
            ok(f"Copied dir: {src} → {dst}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _sh.copy2(src, dst)
            ok(f"Copied: {src} → {dst}")

    elif name in ("/mv", "/move"):
        parts2 = arg.split()
        if len(parts2) < 2:
            warn("Usage: /mv <src> <dst>"); return True
        src, dst = Path(parts2[0]).expanduser(), Path(parts2[1]).expanduser()
        if not src.exists():
            error(f"Not found: {src}"); return True
        import shutil as _sh
        dst.parent.mkdir(parents=True, exist_ok=True)
        _sh.move(str(src), str(dst))
        ok(f"Moved: {src} → {dst}")

    elif name in ("/rm", "/del"):
        if not arg:
            warn("Usage: /rm <path>"); return True
        p = Path(arg).expanduser()
        if not p.exists():
            error(f"Not found: {p}"); return True
        if not confirm_action(f"Delete {p}?"):
            return True
        import shutil as _sh
        if p.is_dir():
            _sh.rmtree(p)
            ok(f"Deleted dir: {p}")
        else:
            p.unlink()
            ok(f"Deleted: {p}")

    elif name in ("/touch", "/new"):
        if not arg:
            warn("Usage: /touch <file>"); return True
        p = Path(arg).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        ok(f"Created: {p}")

    elif name in ("/rename", "/rn"):
        parts2 = arg.split(None, 1)
        if len(parts2) < 2:
            warn("Usage: /rename <old> <new>"); return True
        src, dst = Path(parts2[0]).expanduser(), Path(parts2[1]).expanduser()
        src.rename(dst)
        ok(f"Renamed: {src} → {dst}")

    elif name == "/find":
        if not arg:
            warn("Usage: /find <pattern> [path]"); return True
        parts2 = arg.split(None, 1)
        pattern = parts2[0]
        search_path = parts2[1] if len(parts2) > 1 else session.cwd
        run_shell(f"find {search_path} -name '{pattern}' 2>/dev/null | head -40", cwd=session.cwd)

    elif name == "/tree":
        target = arg or session.cwd
        run_shell(f"find {target} -not -path '*/venv/*' -not -path '*/__pycache__/*' -not -path '*/.git/*' | head -80 | sort", cwd=session.cwd)

    elif name in ("/chmod", "/chown"):
        if not arg:
            warn(f"Usage: {name} <perms/owner> <path>"); return True
        run_shell(f"sudo {name[1:]} {arg}", cwd=session.cwd)

    elif name == "/symlink":
        parts2 = arg.split(None, 1)
        if len(parts2) < 2:
            warn("Usage: /symlink <target> <link_name>"); return True
        run_shell(f"ln -sf {parts2[0]} {parts2[1]}", cwd=session.cwd)

    elif name in ("/zip", "/tar"):
        parts2 = arg.split(None, 1)
        if len(parts2) < 2:
            warn(f"Usage: {name} <output> <source>"); return True
        out, src = parts2[0], parts2[1]
        if name == "/zip":
            run_shell(f"zip -r {out} {src}", cwd=session.cwd)
        else:
            run_shell(f"tar -czf {out} {src}", cwd=session.cwd)

    elif name == "/unzip":
        parts2 = arg.split(None, 1)
        if not parts2:
            warn("Usage: /unzip <file> [dest]"); return True
        archive = parts2[0]
        dest    = parts2[1] if len(parts2) > 1 else "."
        if archive.endswith(".zip"):
            run_shell(f"unzip -o {archive} -d {dest}", cwd=session.cwd)
        else:
            run_shell(f"tar -xzf {archive} -C {dest}", cwd=session.cwd)

    elif name == "/env":
        if arg:
            val = os.environ.get(arg, "(not set)")
            print(f"  {cyan(arg)} = {val}")
        else:
            for k, v in sorted(os.environ.items()):
                if any(x in k.upper() for x in ["KEY","TOKEN","SECRET","PASSWORD","PASS"]):
                    v = "●" * 8
                print(f"  {dim(k)}={v}")

    elif name == "/pip":
        if not arg:
            warn("Usage: /pip <install|list|show> [package]"); return True
        venv_pip = os.path.join(FRAMEWORK_DIR, "venv", "bin", "pip")
        pip_cmd  = venv_pip if os.path.exists(venv_pip) else "pip3"
        run_shell(f"{pip_cmd} {arg}", cwd=session.cwd)

    elif name == "/apt":
        if not arg:
            warn("Usage: /apt <install|update|search> [package]"); return True
        run_shell(f"sudo apt {arg} -y", cwd=session.cwd)

    elif name in ("/ps", "/top"):
        if name == "/ps":
            run_shell("ps aux --sort=-%cpu | head -20", cwd=session.cwd)
        else:
            run_shell("top -bn1 | head -20", cwd=session.cwd)

    elif name == "/kill":
        if not arg:
            warn("Usage: /kill <pid|name>"); return True
        if arg.isdigit():
            run_shell(f"kill -9 {arg}", cwd=session.cwd)
        else:
            run_shell(f"pkill -f {arg}", cwd=session.cwd)

    elif name == "/port":
        if arg:
            run_shell(f"sudo ss -tlnp | grep {arg}", cwd=session.cwd)
        else:
            run_shell("sudo ss -tlnp | grep LISTEN", cwd=session.cwd)

    elif name == "/curl":
        if not arg:
            warn("Usage: /curl <url> [flags]"); return True
        run_shell(f"curl -sL {arg}", cwd=session.cwd)

    elif name == "/wget":
        if not arg:
            warn("Usage: /wget <url> [dest]"); return True
        run_shell(f"wget {arg}", cwd=session.cwd)

    elif name == "/ssh":
        if not arg:
            warn("Usage: /ssh <host> [cmd]"); return True
        run_shell(f"ssh {arg}", capture=False)

    elif name == "/scp":
        if not arg:
            warn("Usage: /scp <src> <dst>"); return True
        run_shell(f"scp {arg}", cwd=session.cwd)

    elif name == "/rsync":
        if not arg:
            warn("Usage: /rsync <src> <dst>"); return True
        run_shell(f"rsync -avz --progress {arg}", cwd=session.cwd)

    elif name == "/deploy":
        # Smart deploy: detect project type and run appropriate deploy command
        target = arg or session.cwd
        p = Path(target).expanduser()
        info(f"Detecting project type in {p}...")
        if (p / "docker-compose.yml").exists() or (p / "docker-compose.yaml").exists():
            run_shell(f"cd {p} && docker compose up -d --build", cwd=str(p))
        elif (p / "Dockerfile").exists():
            svc = p.name
            run_shell(f"cd {p} && docker build -t {svc} . && docker run -d --name {svc} {svc}", cwd=str(p))
        elif (p / "requirements.txt").exists():
            venv_pip = os.path.join(str(p), "venv", "bin", "pip")
            pip = venv_pip if os.path.exists(venv_pip) else "pip3"
            run_shell(f"cd {p} && {pip} install -r requirements.txt", cwd=str(p))
        elif (p / "package.json").exists():
            run_shell(f"cd {p} && npm install && npm run build 2>/dev/null || npm start", cwd=str(p))
        elif (p / "setup.py").exists() or (p / "pyproject.toml").exists():
            run_shell(f"cd {p} && pip install -e .", cwd=str(p))
        else:
            warn("No recognized project type. Specify: /deploy <path>")
            info("Supported: docker-compose, Dockerfile, requirements.txt, package.json, setup.py")

    elif name == "/restart":
        # Convenience: restart one or all baza services
        if arg:
            run_shell(f"sudo systemctl restart {arg}", cwd=session.cwd)
        else:
            run_shell("sudo systemctl restart baza-dashboard baza-agent-simon-bately baza-agent-claw-batto", cwd=session.cwd)

    elif name == "/logs":
        svc = arg or "baza-dashboard"
        run_shell(f"journalctl -u {svc} -f --no-pager -n 30", capture=False)

    elif name == "/reload":
        # Reload framework: restart all baza-agent-* services
        if arg:
            run_shell(f"sudo systemctl restart baza-agent-{arg.replace('_','-')}", cwd=session.cwd)
        else:
            run_shell("sudo systemctl restart $(systemctl list-units 'baza-agent-*' --no-legend | awk '{print $1}')", cwd=session.cwd)

    elif name == "/backup":
        src  = arg if arg else session.cwd
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = f"{src}_backup_{ts}"
        import shutil as _sh
        _sh.copytree(src, dest, dirs_exist_ok=False)
        ok(f"Backup created: {dest}")

    elif name == "/disk":
        run_shell("df -h | grep -v tmpfs | grep -v udev", cwd=session.cwd)
        run_shell("du -sh /* 2>/dev/null | sort -rh | head -15", cwd=session.cwd)

    elif name == "/mem":
        run_shell("free -h", cwd=session.cwd)
        run_shell("cat /proc/meminfo | grep -E 'MemTotal|MemFree|MemAvailable|Cached'", cwd=session.cwd)

    elif name == "/net":
        run_shell("ip addr show | grep -E 'inet |link/'", cwd=session.cwd)
        run_shell("ss -s", cwd=session.cwd)

    elif name in ("/ping",):
        host = arg or "8.8.8.8"
        run_shell(f"ping -c 4 {host}", cwd=session.cwd)

    elif name == "/cron":
        # Quick cron management from CLI
        parts2 = arg.split(None, 2) if arg else []
        action = parts2[0] if parts2 else "list"
        if action == "list":
            run_shell("crontab -l", cwd=session.cwd)
        elif action == "edit":
            run_shell("crontab -e", capture=False)
        elif action in ("add",) and len(parts2) == 3:
            # /cron add "0 8 * * *" "command"
            schedule, cmd = parts2[1], parts2[2]
            raw  = subprocess.run(["crontab","-l"], capture_output=True, text=True).stdout
            new  = raw + f"# baza-empire-managed name=cli_job_{int(datetime.now().timestamp())}\n{schedule} {cmd}\n"
            proc = subprocess.run(["crontab","-"], input=new, capture_output=True, text=True)
            if proc.returncode == 0: ok(f"Cron added: {schedule} {cmd}")
            else: error(f"Failed: {proc.stderr}")
        elif action == "rm" and len(parts2) >= 2:
            run_shell(f"crontab -l | grep -v '{parts2[1]}' | crontab -", cwd=session.cwd)
        else:
            info("Usage: /cron list | /cron edit | /cron add '<schedule>' '<cmd>' | /cron rm '<pattern>'")


    # ── New super-agent commands ────────────────────────────────────────────────

    elif name == "/agent":
        # Dispatch a task to another Baza agent via Telegram
        if not arg:
            warn("Usage: /agent <agent_id> <task>  (e.g. /agent simon_bately Review the SSL cert)")
            return True
        parts2 = arg.split(None, 1)
        if len(parts2) < 2:
            warn("Usage: /agent <agent_id> <task>"); return True
        target_id, task_msg = parts2[0].lower(), parts2[1].strip()
        # Map short names to env vars
        token_map = {
            "simon": "TELEGRAM_SIMON_BATELY", "simon_bately": "TELEGRAM_SIMON_BATELY",
            "claw":  "TELEGRAM_CLAW_BATTO",   "claw_batto":   "TELEGRAM_CLAW_BATTO",
            "phil":  "TELEGRAM_PHIL_HASS",    "phil_hass":    "TELEGRAM_PHIL_HASS",
            "sam":   "TELEGRAM_SAM_AXE",      "sam_axe":      "TELEGRAM_SAM_AXE",
            "rex":   "TELEGRAM_REX_VALOR",    "rex_valor":    "TELEGRAM_REX_VALOR",
            "duke":  "TELEGRAM_DUKE_HARMON",  "duke_harmon":  "TELEGRAM_DUKE_HARMON",
            "scout": "TELEGRAM_SCOUT_REEVES", "scout_reeves": "TELEGRAM_SCOUT_REEVES",
            "nova":  "TELEGRAM_NOVA_STERLING","nova_sterling": "TELEGRAM_NOVA_STERLING",
        }
        env_var = token_map.get(target_id)
        if not env_var:
            error(f"Unknown agent: {target_id}. Known: simon, claw, phil, sam, rex, duke, scout, nova")
            return True
        token = os.environ.get(env_var)
        if not token:
            error(f"Token env var {env_var} not set — load secrets.env first")
            return True
        # Get Serge's chat_id for the agent (stored in memory DB)
        serge_chat_id = os.environ.get("SERGE_CHAT_ID", "")
        if not serge_chat_id:
            warn("SERGE_CHAT_ID not set — cannot send Telegram message")
            return True
        import urllib.parse as _urlparse
        payload = json.dumps({"chat_id": serge_chat_id, "text": task_msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    ok(f"Dispatched to {target_id}: {task_msg[:80]}")
                else:
                    error(f"Telegram error: {result.get('description','unknown')}")
        except Exception as e:
            error(f"Failed to dispatch: {e}")

    elif name == "/skill":
        sub = arg.split(None, 1) if arg else []
        action = sub[0].lower() if sub else "list"
        rest = sub[1].strip() if len(sub) > 1 else ""

        if action == "list":
            shared = os.path.join(FRAMEWORK_DIR, "skills", "shared")
            agent_dir = os.path.join(FRAMEWORK_DIR, "agents", "claw_batto", "skills")
            print(cyan("\n  Shared skills:"))
            for f in sorted(Path(shared).glob("*.py")):
                print(f"    {f.stem}")
            if os.path.isdir(agent_dir):
                print(cyan("  Claw-specific skills:"))
                for f in sorted(Path(agent_dir).glob("*.py")):
                    print(f"    {f.stem}")
            print()

        elif action == "run":
            parts2 = rest.split(None, 1)
            skill_name = parts2[0] if parts2 else ""
            skill_args = {}
            if len(parts2) > 1:
                try: skill_args = json.loads(parts2[1])
                except: warn(f"Args must be JSON: {parts2[1]}")
            if not skill_name:
                warn("Usage: /skill run <name> [json_args]"); return True
            import subprocess as _sp
            shared = os.path.join(FRAMEWORK_DIR, "skills", "shared")
            agent_dir = os.path.join(FRAMEWORK_DIR, "agents", "claw_batto", "skills")
            skill_path_found = None
            for base in [agent_dir, shared]:
                candidate = os.path.join(base, skill_name + ".py")
                if os.path.exists(candidate):
                    skill_path_found = candidate
                    break
            if not skill_path_found:
                error(f"Skill not found: {skill_name}"); return True
            env = os.environ.copy()
            env["SKILL_ARGS"] = json.dumps(skill_args)
            env["AGENT_ID"] = "claw_batto"
            venv_py = os.path.join(FRAMEWORK_DIR, "venv", "bin", "python3")
            py = venv_py if os.path.exists(venv_py) else "python3"
            result = _sp.run([py, skill_path_found], capture_output=True, text=True, timeout=30, env=env)
            if result.returncode == 0:
                print(green(f"\n  [{skill_name} output]"))
                print(textwrap.indent(result.stdout.strip(), "  "))
            else:
                error(f"Skill failed (exit {result.returncode}):")
                print(red(textwrap.indent(result.stderr.strip()[:1000], "  ")))

        elif action == "create":
            skill_name = rest.strip() or input(f"  {cyan('Skill name (snake_case):')} ").strip()
            if not skill_name:
                warn("Need a skill name"); return True
            template = (
                f'#!/usr/bin/env python3\n'
                f'"""Baza Empire Skill — {skill_name}\nTODO: describe what this skill does.\n"""\n'
                f'import os, json, sys\n\n'
                f'args = json.loads(os.environ.get("SKILL_ARGS", "{{}}"))\n\n'
                f'# TODO: implement skill logic here\n'
                f'result = "hello from {skill_name}"\n\n'
                f'print(result)\n'
            )
            dest = os.path.join(FRAMEWORK_DIR, "skills", "shared", f"{skill_name}.py")
            if os.path.exists(dest) and not confirm_action(f"Overwrite existing skill {skill_name}?"):
                return True
            Path(dest).write_text(template)
            import stat as _stat
            os.chmod(dest, os.stat(dest).st_mode | _stat.S_IXUSR)
            ok(f"Created: {dest}")
            info("Edit the file to implement, then test with: /skill run " + skill_name)
            # Load into context for editing
            session.ctx.load_file(dest)
        else:
            warn("Usage: /skill list | /skill run <name> [args] | /skill create <name>")

    elif name == "/tasks":
        import sqlite3 as _sqlite3
        db_path = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
        if not os.path.exists(db_path):
            error(f"Task DB not found: {db_path}"); return True
        status_filter = arg.strip() if arg else None
        try:
            conn = _sqlite3.connect(db_path)
            conn.row_factory = _sqlite3.Row
            cur = conn.cursor()
            if status_filter:
                cur.execute("SELECT id, title, assigned_to, status, priority, project_id FROM tasks WHERE status=? ORDER BY priority DESC, created_at DESC LIMIT 30", (status_filter,))
            else:
                cur.execute("SELECT id, title, assigned_to, status, priority, project_id FROM tasks ORDER BY status, priority DESC, created_at DESC LIMIT 40")
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            error(f"DB error: {e}"); return True
        if not rows:
            info("No tasks found" + (f" with status={status_filter}" if status_filter else "")); return True
        STATUS_ICON = {"completed": "✅", "in_progress": "🔄", "pending": "⏳", "blocked": "🚨"}
        print(cyan(f"\n  Tasks ({len(rows)}):\n"))
        for r in rows:
            icon = STATUS_ICON.get(r["status"], "•")
            pid = (r["project_id"] or "")[-12:]
            print(f"  {icon} [{r['id'][:8]}] {r['title'][:55]:<55} {dim(r['assigned_to'] or '?'):<15} {dim(pid)}")
        print()

    elif name == "/memory":
        parts2 = arg.split(None, 1) if arg else []
        try:
            sys.path.insert(0, FRAMEWORK_DIR)
            from core.context_db import memory_get_all, memory_set, memory_get
        except Exception as e:
            error(f"Cannot connect to context DB: {e}"); return True
        if not parts2:
            # Show all memory
            mem = memory_get_all("claw_batto")
            if not mem:
                info("No memory stored for claw_batto yet"); return True
            print(cyan("\n  Claw memory:\n"))
            for key, data in sorted(mem.items()):
                cat = dim(f"[{data['category']}]")
                print(f"  {cat} {cyan(key)}: {data['value'][:80]}")
            print()
        elif len(parts2) == 1:
            # Get single key
            val = memory_get("claw_batto", parts2[0])
            if val:
                print(f"  {cyan(parts2[0])}: {val}")
            else:
                info(f"No memory for key: {parts2[0]}")
        else:
            # Set key = value
            memory_set("claw_batto", parts2[0], parts2[1])
            ok(f"Memory set: {parts2[0]} = {parts2[1][:60]}")

    elif name == "/db":
        if not arg:
            warn("Usage: /db <sql query>  (e.g. /db SELECT * FROM tasks WHERE status='pending' LIMIT 5)")
            return True
        import sqlite3 as _sqlite3
        db_path = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
        if not os.path.exists(db_path):
            error(f"DB not found: {db_path}"); return True
        try:
            conn = _sqlite3.connect(db_path)
            conn.row_factory = _sqlite3.Row
            cur = conn.cursor()
            cur.execute(arg)
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            error(f"Query error: {e}"); return True
        if not rows:
            info("(no rows returned)"); return True
        # Print as table
        cols = rows[0].keys()
        col_widths = {c: max(len(c), max(len(str(r[c] or "")) for r in rows)) for c in cols}
        header = "  " + " | ".join(c.ljust(col_widths[c]) for c in cols)
        divider_line = "  " + "-+-".join("-" * col_widths[c] for c in cols)
        print(cyan(f"\n  {header}"))
        print(dim(divider_line))
        for r in rows[:50]:
            print("  " + " | ".join(str(r[c] or "").ljust(col_widths[c])[:col_widths[c]] for c in cols))
        if len(rows) > 50:
            info(f"  ... {len(rows)-50} more rows")
        print()

    elif name == "/commit":
        # AI-assisted git commit
        if arg:
            # Direct commit with provided message
            run_shell(f"git -C {session.cwd} add -u && git -C {session.cwd} commit -m '{arg}'", cwd=session.cwd)
        else:
            # Generate commit message from staged diff
            rc, diff_out = run_shell(f"git -C {session.cwd} diff --staged", cwd=session.cwd)
            if not diff_out.strip():
                warn("No staged changes. Use: git add <files> first, or /commit <message>")
                return True
            truncated = diff_out[:4000]
            msgs = [{"role": "user", "content":
                f"Write a concise git commit message for this diff.\n"
                f"Format: one short subject line (50 chars max), then blank line, then 1-3 bullet points explaining what changed and why.\n"
                f"Diff:\n{truncated}"}]
            info("Generating commit message...")
            commit_msg = ollama_chat(msgs, stream=False)
            if not commit_msg:
                warn("Could not generate message — use /commit <message>"); return True
            print(f"\n{bold('  Proposed commit message:')}")
            print(textwrap.indent(commit_msg.strip(), "    "))
            print()
            if confirm_action("Commit with this message?"):
                # Write to temp file to handle multiline
                tmp = "/tmp/claw_commit_msg.txt"
                Path(tmp).write_text(commit_msg.strip())
                run_shell(f"git -C {session.cwd} commit -F {tmp}", cwd=session.cwd)

    elif name == "/review":
        target = arg if arg else session.cwd
        p = Path(target).expanduser()
        if not p.exists():
            error(f"Not found: {target}"); return True
        ctx_before = dict(session.ctx.files)
        if p.is_dir():
            session.ctx.load_dir(str(p))
        else:
            session.ctx.load_file(str(p))
        file_ctx = session.ctx.get_context()
        session.ctx.files = ctx_before  # restore context so review doesn't pollute it
        if not file_ctx:
            warn("No files loaded for review"); return True
        info(f"Reviewing: {target}")
        msgs = [{"role": "user", "content":
            f"Do a thorough code review of this code. Cover:\n"
            f"1. Bugs and logic errors (most important)\n"
            f"2. Security issues\n"
            f"3. Performance problems\n"
            f"4. Missing error handling\n"
            f"5. Code quality and maintainability\n\n"
            f"For each issue: state the file+line, describe the problem, and give the fix.\n"
            f"Be specific. Don't comment on style unless it causes bugs.\n\n"
            f"Code to review:\n{file_ctx[:6000]}"}]
        resp = ollama_chat(msgs)
        if resp:
            session.add("user", msgs[0]["content"])
            session.add("assistant", resp)

    elif name == "/summarize":
        if not session.messages:
            info("Nothing to summarize yet"); return True
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content'][:200]}"
            for m in session.messages[-20:]
        )
        msgs = [{"role": "user", "content":
            f"Summarize this CLI session in 3-5 bullet points. "
            f"Focus on: what was built/fixed, key decisions, files written, commands run.\n\n{history_text}"}]
        info("Summarizing session...")
        summary = ollama_chat(msgs, stream=False)
        if summary:
            print(f"\n{bold(cyan('  Session Summary:'))}")
            print(textwrap.indent(summary.strip(), "  "))
            # Save to Claw memory if DB available
            try:
                sys.path.insert(0, FRAMEWORK_DIR)
                from core.context_db import memory_set
                from datetime import datetime as _dt
                key = f"cli_session_{_dt.now().strftime('%Y%m%d_%H%M')}"
                memory_set("claw_batto", key, summary.strip()[:500], "sessions")
                ok(f"Saved to memory: {key}")
            except Exception:
                pass
            print()

    elif name == "/init":
        # Scan project, generate CLAW.md with project context for Claw
        target = Path(arg).expanduser() if arg else Path(session.cwd)
        if not target.is_dir():
            error(f"Not a directory: {target}"); return True

        claw_md = target / "CLAW.md"
        if claw_md.exists() and not confirm_action("CLAW.md already exists. Regenerate?"):
            return True

        info(f"Scanning project: {target}")

        # Detect project signals
        markers = ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile",
                   "package.json", "Cargo.toml", "go.mod",
                   "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                   "Makefile", ".env.example", "README.md", "CLAUDE.md", "CLAW.md"]
        found_markers = [m for m in markers if (target / m).exists()]

        # Read key files for context
        ctx_parts: list[str] = []
        for fname in ["README.md", "requirements.txt", "pyproject.toml",
                      "package.json", "Makefile"]:
            p = target / fname
            if p.exists():
                snippet = p.read_text(errors="ignore")[:2500]
                ctx_parts.append(f"=== {fname} ===\n{snippet}")

        # Get shallow directory tree
        rc, tree_out = run_shell(
            f"find '{target}' -maxdepth 2 "
            f"-not -path '*/venv/*' -not -path '*/__pycache__/*' "
            f"-not -path '*/.git/*' -not -path '*/node_modules/*' "
            f"-not -path '*/.mypy_cache/*' | sort | head -70",
            capture=True
        )

        file_ctx = "\n\n".join(ctx_parts)

        info("Analysing project with AI — generating CLAW.md...")
        msgs = [{"role": "user", "content":
            f"Analyse this project and write a CLAW.md file.\n"
            f"CLAW.md is loaded by Claw Batto (an AI coding CLI) as project context "
            f"at the start of every session. Make it dense and practical.\n\n"
            f"Project root: {target}\n"
            f"Project markers found: {', '.join(found_markers) or 'none'}\n\n"
            f"Directory tree:\n{tree_out[:2500]}\n\n"
            f"Key file contents:\n{file_ctx[:3000]}\n\n"
            f"Generate CLAW.md with exactly these sections (no extras):\n\n"
            f"# Project\n"
            f"One-line summary. Stack in bullet points.\n\n"
            f"# Architecture\n"
            f"Key files/dirs with one-line purpose each.\n\n"
            f"# Run & Test\n"
            f"Exact commands to run dev, run tests, build.\n\n"
            f"# Conventions\n"
            f"Code style rules, patterns Claw must follow.\n\n"
            f"# Claw Notes\n"
            f"Anything a senior dev joining this project needs to know immediately.\n\n"
            f"Be terse. No filler. Senior engineer tone."}]

        claw_content = ollama_chat(msgs, stream=True)
        if not claw_content:
            error("LLM returned empty — check Ollama"); return True

        claw_content = claw_content.strip()
        dest_str = str(claw_md)
        session.ctx.stage(dest_str, claw_content)

        print()
        print(f"  {bold(yellow('━' * 46))}")
        print(f"  {bold(yellow('  ✦  CLAW.md staged'))}  {dim(dest_str)}")
        print(f"  {yellow('━' * 46)}")
        print(f"  {steel('/diff')}   {dim('review changes')}")
        print(f"  {steel('/apply')}  {dim('write to disk')}")
        print(f"  {yellow('━' * 46)}\n")

        # Auto-load CLAW.md into context once written
        if claw_md.exists():
            session.ctx.load_file(dest_str)

    # ── v2 power commands ───────────────────────────────────────────────────────

    elif name == "/outline":
        # Show code structure (classes + functions) for loaded Python files
        target_files = {}
        if arg:
            for p, c in session.ctx.files.items():
                if arg in p:
                    target_files[p] = c
            if not target_files:
                error(f"No loaded file matches: {arg}"); return True
        elif session.ctx.files:
            target_files = {p: c for p, c in session.ctx.files.items() if p.endswith(".py")}
        if not target_files:
            warn("No Python files loaded. Use /file or /dir first."); return True
        print(yellow(f"\n  Code outline — {len(target_files)} file(s)\n"))
        for fpath, content in target_files.items():
            print(f"  {bold(yellow(Path(fpath).name))}  {dim(fpath)}")
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        methods = [n for n in node.body
                                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                        print(f"    {magenta('class')} {bold(node.name)}  "
                              f"{dim(f'L{node.lineno}')}  {steel(f'({len(methods)} methods)')}")
                        for m in methods:
                            a = ", ".join(a.arg for a in m.args.args)[:35]
                            print(f"      {yellow('↳')} {m.name}({a})  {dim(f'L{m.lineno}')}")
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        a = ", ".join(x.arg for x in node.args.args)[:40]
                        print(f"    {steel('def')} {white(node.name)}({a})  {dim(f'L{node.lineno}')}")
            except SyntaxError as e:
                warn(f"  Parse error: {e}")
            print()

    elif name == "/tokens":
        # Estimate context token usage
        ctx_text  = session.ctx.get_context()
        msg_text  = " ".join(m["content"] for m in session.messages)
        all_text  = CLAW_SYSTEM + ctx_text + msg_text
        est       = len(all_text) // 4
        limit     = CONTEXT_LIMIT
        pct       = min(100, int(est / max(limit, 1) * 100))
        bar_len   = 32
        filled    = int(bar_len * pct / 100)
        bar       = "█" * filled + "░" * (bar_len - filled)
        bar_color = green if pct < 55 else yellow if pct < 80 else red
        print(f"\n  {bold(yellow('Context usage'))}")
        print(f"  {bar_color(bar)}  {bold(str(pct))}%")
        print(f"  {steel('Est. tokens')}  {white(f'{est:,}')} / {dim(f'{limit:,}')}")
        print(f"  {steel('Messages')}     {white(str(len(session.messages)))}")
        print(f"  {steel('Files')}        {white(str(len(session.ctx.files)))}")
        print(f"  {steel('Staged')}       {white(str(len(session.ctx.pending)))}")
        print()

    elif name in ("/fmt", "/format"):
        # Format Python files with black
        target = arg if arg else None
        venv_black = os.path.join(FRAMEWORK_DIR, "venv", "bin", "black")
        black_bin  = venv_black if os.path.exists(venv_black) else "black"
        if target:
            p = Path(target).expanduser()
            if not p.exists():
                error(f"File not found: {target}"); return True
            run_shell(f"{black_bin} '{p}' --line-length 100 2>&1", cwd=session.cwd)
            if str(p.resolve()) in session.ctx.files:
                session.ctx.load_file(str(p))
        elif session.ctx.pending:
            info("Formatting staged Python files with black...")
            for staged_p, content in list(session.ctx.pending.items()):
                if not staged_p.endswith(".py"): continue
                with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
                    tf.write(content); tfname = tf.name
                rc, _ = run_shell(f"{black_bin} '{tfname}' --line-length 100 -q 2>&1",
                                  cwd=session.cwd)
                if rc == 0:
                    session.ctx.pending[staged_p] = Path(tfname).read_text()
                    ok(f"Formatted: {Path(staged_p).name}")
                else:
                    warn(f"black failed on {Path(staged_p).name}")
                os.unlink(tfname)
        else:
            warn("Usage: /fmt <file.py>  or run with staged files pending")

    elif name == "/lint":
        # Run flake8 linter
        target = arg if arg else "."
        venv_fl = os.path.join(FRAMEWORK_DIR, "venv", "bin", "flake8")
        flake   = venv_fl if os.path.exists(venv_fl) else "flake8"
        run_shell(f"{flake} {target} --max-line-length=100 "
                  f"--exclude=venv,.git,__pycache__ 2>&1 | head -50", cwd=session.cwd)

    elif name in ("/palette", "/pal"):
        # Fuzzy command palette search
        if not arg:
            # Show all commands grouped
            groups = {
                "File context":   ["/file","/dir","/cat","/write","/diff","/apply","/search","/grep"],
                "File ops":       ["/mkdir","/cp","/mv","/rm","/touch","/rename","/find","/tree"],
                "Code tools":     ["/outline","/tokens","/fmt","/lint","/fix","/review","/test"],
                "AI features":    ["/composer","/explain","/summarize","/commit"],
                "Shell & system": ["/run","/cd","/ls","/ps","/kill","/port","/infra","/disk","/mem"],
                "Services":       ["/svc","/restart","/reload","/logs","/deploy"],
                "Baza Empire":    ["/agent","/skill","/tasks","/memory","/db"],
                "Session":        ["/context","/clear","/save","/load","/model","/models","/doctor"],
            }
            print(yellow(f"\n  {'─'*55}"))
            print(bold(yellow(f"  Command Palette — Claw Batto v{CLAW_VERSION}")))
            print(yellow(f"  {'─'*55}"))
            for grp, cmds in groups.items():
                print(f"\n  {bold(steel(grp))}")
                row = "  " + "  ".join(yellow(c) for c in cmds)
                print(row)
            print(yellow(f"\n  {'─'*55}"))
            print(dim("  /palette <query>  to fuzzy search"))
        else:
            q = arg.lower()
            hits = [c for c in _SLASH_COMMANDS if q in c.lower()]
            if hits:
                print(yellow(f"\n  Matches for '{q}':\n"))
                for c in hits:
                    print(f"    {yellow(c)}")
                print()
            else:
                info("No matching commands")

    elif name == "/btw":
        # Inject a background note/context into the session without triggering a response
        # Usage: /btw this file uses the legacy API, not the new one
        if not arg:
            warn("Usage: /btw <note>  — adds a silent context note to the session")
            return True
        note = f"[Background note from Serge: {arg}]"
        session.add("user", note)
        # Add a minimal ack so the message pair stays valid
        session.add("assistant", f"[Got it — noted: {arg[:80]}]")
        print(f"  {steel('Noted:')} {dim(arg)}")

    elif name == "/status":
        # Dashboard-style status overview
        print(yellow(f"\n  {'─'*55}"))
        print(bold(yellow(f"  Claw Batto v{CLAW_VERSION}  ·  Status Dashboard")))
        print(yellow(f"  {'─'*55}"))
        print(f"  {steel('Model')}    {bold(white(CLAW_MODEL))}")
        print(f"  {steel('Ollama')}   {green('online') if ollama_available() else red('offline')}  {dim(OLLAMA_HOST)}")
        print(f"  {steel('CWD')}      {dim(session.cwd)}")
        print(f"  {steel('Files')}    {white(str(len(session.ctx.files)))} loaded"
              + (f"  {yellow(str(len(session.ctx.pending)) + ' staged')}" if session.ctx.pending else ""))
        print(f"  {steel('Context')}  {white(str(len(session.messages)))} messages")
        if session.name:
            print(f"  {steel('Session')}  {white(session.name)}")
        # Quick service check
        print(f"\n  {bold(steel('Services'))}")
        for svc, label in [("baza-dashboard", "dashboard"),
                           ("baza-agent-simon-bately", "simon"),
                           ("baza-agent-claw-batto", "claw"),
                           ("ollama", "ollama")]:
            rc, stat = run_shell(f"systemctl is-active {svc} 2>/dev/null",
                                 capture=True)
            st = stat.strip()
            color = green if st == "active" else yellow if st == "activating" else red
            print(f"    {color('●')} {label:<20} {dim(st)}")
        print(yellow(f"  {'─'*55}\n"))

    elif name in ("/help", "/?"):
        W = 64
        def _row(left: str, right: str) -> str:
            bare_l = re.sub(r'\033\[[0-9;]*m', '', left)
            pad = max(0, 22 - len(bare_l))
            return (yellow("  ║  ") + yellow(left) + " " * pad
                    + dim(right) + yellow("  ║"))
        def _head(title: str) -> str:
            return (yellow("  ╠" + "═" * (W - 2) + "╣\n")
                    + yellow("  ║  ") + bold(accent(f"  {title}"))
                    + " " * max(0, W - 6 - len(title))
                    + yellow("  ║"))
        lines = [
            yellow("  ╔" + "═" * (W - 2) + "╗"),
            yellow("  ║") + bold(accent(f"  Claw Batto v{CLAW_VERSION}  —  Command Reference".center(W - 2))) + yellow("║"),
            _head("FILE CONTEXT"),
            _row("/init [path]",        "scan project → generate CLAW.md"),
            _row("/file <path>",        "load file into context"),
            _row("/dir [path]",         "load whole directory"),
            _row("/cat <file>",         "display with syntax highlight"),
            _row("/outline [file]",     "show classes + functions"),
            _row("/write <path>",       "stage last code to file"),
            _row("/diff [path]",        "show staged changes"),
            _row("/apply [path]",       "write staged → disk"),
            _row("/search <pat>",       "search loaded files"),
            _row("/grep <pat> [path]",  "grep filesystem"),
            _head("CODE TOOLS  ★v2"),
            _row("/outline [file]",     "AST class/function structure"),
            _row("/tokens",             "context usage bar + estimates"),
            _row("/fmt [file]",         "format with black (staged ok)"),
            _row("/lint [path]",        "run flake8 linter"),
            _row("/palette [query]",    "command palette / fuzzy search"),
            _row("/status",             "full dashboard: model+services"),
            _head("FILE OPS"),
            _row("/mkdir /cp /mv /rm",  "create/copy/move/delete"),
            _row("/touch /rename",      "create/rename"),
            _row("/find /tree",         "find by name / show tree"),
            _row("/zip /unzip",         "archive/extract"),
            _row("/backup [path]",      "timestamped backup"),
            _head("SHELL & SYSTEM"),
            _row("/run <cmd>",          "run any shell command"),
            _row("/cd /ls /env",        "navigation + environment"),
            _row("/ps /kill /port",     "processes + ports"),
            _row("/disk /mem /net",     "resource stats"),
            _row("/ping /cron",         "ping + cron management"),
            _head("DEPLOY & SERVICES"),
            _row("/deploy [path]",      "smart deploy docker/pip/npm"),
            _row("/restart [svc]",      "restart service(s)"),
            _row("/reload [agent]",     "reload baza agent services"),
            _row("/logs [svc]",         "stream service logs live"),
            _row("/svc <act> <svc>",    "manage systemd service"),
            _row("/infra",              "quick infra status"),
            _row("/pip /apt",           "package management"),
            _head("GIT"),
            _row("/git [cmd]",          "any git command"),
            _row("/commit [msg]",       "AI-assisted commit"),
            _head("REMOTE"),
            _row("/ssh /scp /rsync",    "remote access + sync"),
            _row("/curl /wget",         "HTTP + download"),
            _head("BAZA EMPIRE"),
            _row("/agent <id> <task>",  "dispatch to another agent"),
            _row("/skill list|run|create", "manage skills"),
            _row("/tasks [status]",     "view project tasks"),
            _row("/memory [key] [val]", "persistent memory"),
            _row("/db <sql>",           "SQLite query baza_projects.db"),
            _head("AI FEATURES"),
            _row("/composer <task>",    "step-by-step guided build"),
            _row("/fix <error>",        "fix an error or bug"),
            _row("/explain",            "explain last response"),
            _row("/review [file|dir]",  "AI code review"),
            _row("/test [path]",        "run pytest"),
            _row("/summarize",          "summarize + save to memory"),
            _head("SESSION"),
            _row("/context",            "show loaded files + stats"),
            _row("/clear",              "full session reset with summary"),
            _row("/btw <note>",         "inject silent context note"),
            _row("/save /load <n>",     "save/load session"),
            _row("/sessions",           "list saved sessions"),
            _row("/model [name]",       "show/set model"),
            _row("/models",             "list Ollama models"),
            _row("/doctor",             "health check services"),
            _row("/watch <file>",       "auto-review on file save"),
            yellow("  ╚" + "═" * (W - 2) + "╝"),
            dim("  Type naturally to chat.  exit/quit to exit."),
        ]
        print("\n" + "\n".join(lines) + "\n")

    else:
        return False

    return True

# ── Tab completion ─────────────────────────────────────────────────────────────
_SLASH_COMMANDS = [
    "/file", "/f", "/dir", "/d", "/cat", "/write", "/diff", "/apply", "/a",
    "/search", "/grep", "/context", "/clear", "/cd", "/ls", "/run", "/r",
    "/mkdir", "/md", "/cp", "/copy", "/mv", "/move", "/rm", "/del",
    "/touch", "/new", "/rename", "/rn", "/find", "/tree", "/zip", "/unzip",
    "/env", "/pip", "/apt", "/ps", "/top", "/kill", "/port", "/curl", "/wget",
    "/ssh", "/scp", "/rsync", "/chmod", "/chown", "/symlink", "/tar",
    "/svc", "/git", "/commit", "/review", "/test", "/infra",
    "/deploy", "/restart", "/reload", "/logs", "/backup", "/disk", "/mem",
    "/net", "/ping", "/cron",
    "/agent", "/skill", "/tasks", "/memory", "/db",
    "/composer", "/fix", "/explain", "/summarize",
    "/model", "/models", "/doctor", "/watch",
    "/save", "/load", "/sessions",
    "/stop", "/s", "/continue", "/c",
    # v2 power commands
    "/init",
    "/outline", "/tokens", "/fmt", "/format", "/lint",
    "/palette", "/pal", "/status",
    "/btw",
    "/help", "/?",
    "exit", "quit",
]

_AGENT_IDS = [
    "simon_bately", "simon", "claw_batto", "claw",
    "phil_hass", "phil", "sam_axe", "sam",
    "rex_valor", "rex", "duke_harmon", "duke",
    "scout_reeves", "scout", "nova_sterling", "nova",
]


def setup_completion(session: 'Session'):
    """Install readline tab-completion: slash commands, file paths, agent names."""
    import glob as _glob

    def completer(text: str, state: int):
        try:
            line = readline.get_line_buffer()
            stripped = line.lstrip()

            # After /agent, complete agent names
            if stripped.startswith("/agent "):
                prefix = stripped[len("/agent "):]
                options = [a for a in _AGENT_IDS if a.startswith(prefix)]
                return (options[state] + " ") if state < len(options) else None

            # After /model, complete Ollama model names
            if stripped.startswith("/model "):
                prefix = stripped[len("/model "):]
                models = ollama_models()
                options = [m for m in models if m.startswith(prefix)]
                return (options[state] + " ") if state < len(options) else None

            # Slash command completion at start of line
            if stripped.startswith("/") and " " not in stripped:
                options = [c for c in _SLASH_COMMANDS if c.startswith(stripped)]
                return (options[state] + " ") if state < len(options) else None

            # File/path completion for commands that take a path argument
            path_cmds = (
                "/file ", "/f ", "/dir ", "/d ", "/cat ", "/write ", "/cd ",
                "/ls ", "/run ", "/review ", "/watch ", "/grep ", "/find ",
                "/cp ", "/mv ", "/rm ", "/backup ", "/scp ", "/rsync ",
                "/chmod ", "/chown ", "/zip ", "/unzip ", "/tar ",
                "/load ", "/logs ",
            )
            for cmd in path_cmds:
                if stripped.startswith(cmd):
                    # Get the partial path being typed
                    rest = stripped[len(cmd):]
                    # If multi-word, take the last token
                    parts = rest.split()
                    path_prefix = parts[-1] if parts else rest
                    matches = _glob.glob(os.path.expanduser(path_prefix) + "*")
                    # Append / for directories
                    options = []
                    for m in sorted(matches):
                        options.append(m + "/" if os.path.isdir(m) else m)
                    return options[state] if state < len(options) else None

            # Default: slash command completion if text starts with /
            if text.startswith("/"):
                options = [c for c in _SLASH_COMMANDS if c.startswith(text)]
                return (options[state] + " ") if state < len(options) else None

            return None
        except Exception:
            return None

    readline.set_completer(completer)
    readline.set_completer_delims(" \t\n")
    readline.parse_and_bind("tab: complete")


# ── Welcome screen ─────────────────────────────────────────────────────────────
def _bare(s: str) -> str:
    """Strip ANSI codes to get printable length."""
    return re.sub(r'\033\[[0-9;]*m', '', s)

def print_welcome(session: 'Session'):
    """Classic single-box welcome — dark red logo, v2 tag, deep blue + bright yellow text."""
    BOX_INNER = 59   # ═ chars between ╔ and ╗

    logo_lines = [
        " ██████╗██╗      █████╗ ██╗    ██╗",
        "██╔════╝██║     ██╔══██╗██║    ██║",
        "██║     ██║     ███████║██║ █╗ ██║",
        "██║     ██║     ██╔══██║██║███╗██║",
        "╚██████╗███████╗██║  ██║╚███╔███╔╝",
        " ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝",
    ]

    def box_row(content_colored: str) -> str:
        """Pad a colored string to BOX_INNER width and wrap in ║ ║."""
        bare_len = len(_bare(content_colored))
        pad      = max(0, BOX_INNER - bare_len)
        return crimson("  ║") + content_colored + " " * pad + crimson("║")

    rows = [
        crimson("  ╔" + "═" * BOX_INNER + "╗"),
        box_row(""),
    ]

    # Logo lines — crimson, last one gets the ⚡v2 tag in scarlet
    for i, line in enumerate(logo_lines):
        indent = "   "
        if i == len(logo_lines) - 1:
            tag      = scarlet(" v2")
            tag_bare = " v2"
            content  = indent + crimson(line) + bold(tag)
        else:
            content = indent + crimson(line)
        rows.append(box_row(content))

    # Spacer + tagline
    rows.append(box_row(""))
    tagline = (cyan("   Baza Empire Dev Agent  |  ")
               + accent("/help")
               + cyan(" for commands"))
    rows.append(box_row(tagline))
    rows.append(crimson("  ╚" + "═" * BOX_INNER + "╝"))

    for r in rows:
        print(r)

    # Status line below box
    print(f"  {dim('model')} {bold(white(CLAW_MODEL))}   "
          f"{dim('cwd')} {dim(session.cwd)}")
    if not ollama_available():
        warn(f"Ollama not responding at {OLLAMA_HOST}")
    print()


# ── REPL ───────────────────────────────────────────────────────────────────────
def repl(initial_task: str = "", initial_file: str = "",
         initial_dir: str = "", composer: bool = False,
         watch: str = "", doctor: bool = False, auto_plan: bool = False):
    session = Session()

    # Readline history
    histfile = os.path.expanduser("~/.claw_history")
    try:
        readline.read_history_file(histfile)
        readline.set_history_length(1000)
    except:
        pass
    import atexit
    atexit.register(readline.write_history_file, histfile)

    # Install tab completion
    setup_completion(session)

    # Ctrl+C handler
    def sigint_handler(sig, frame):
        global _CLAW_STOP
        _CLAW_STOP = True
        print(f"\n{yellow('  [Ctrl+C — stopping output... type to continue or exit to quit]')}")
    signal.signal(signal.SIGINT, sigint_handler)

    if IS_TTY:
        print_welcome(session)

    # ── Auto-init CLAW.md if missing ─────────────────────────────────────────
    if IS_TTY and not doctor:
        claw_md = Path(session.cwd) / "CLAW.md"
        if not claw_md.exists():
            print(cyan("  ┌" + "─" * 54 + "┐"))
            print(cyan("  │") + f"  {bold(white('◆'))}  No {bold(cyan('CLAW.md'))} found in this directory."
                  + " " * 12 + cyan("│"))
            print(cyan("  │") + "     Generate project context file now?         " + cyan("│"))
            print(cyan("  │") + f"  {green('[Y]')} {white('Yes')}   {red('[N]')} {white('Skip')}"
                  + " " * 31 + cyan("│"))
            print(cyan("  └" + "─" * 54 + "┘"))
            try:
                ans = input(f"\n  {cyan('›')} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = 'n'
            if ans in ('y', 'yes', 'ok', ''):
                handle_slash("/init", session)
            else:
                info("Skipped. Run /init anytime to generate CLAW.md.")
            print()

    # Doctor mode
    if doctor:
        run_doctor()
        return

    if initial_file: session.ctx.load_file(initial_file)
    if initial_dir:  session.ctx.load_dir(initial_dir)

    # Watch mode
    if watch:
        watch_mode(watch, session, prompt=initial_task)
        return

    if composer and initial_task:
        composer_mode(initial_task, session)
        return

    if initial_task:
        msg = session.build_user_msg(initial_task)
        session.add("user", msg)
        resp = ollama_chat(session.messages)
        if resp:
            session.last_response = resp
            session.add("assistant", resp)
            blocks = extract_code_blocks(resp)
            if blocks:
                session.last_code = blocks[-1]["content"]
                info(f"Code captured ({len(session.last_code)} chars) — /write <path> to stage")
            writes = extract_write_targets(resp)
            if writes:
                steps = [ProposalStep(i+1, f"Write {Path(w['path']).name}",
                                      "WRITE", w["path"], w.get("content",""))
                         for i, w in enumerate(writes)]
                prop = Proposal("Suggested files", steps)
                ans  = show_proposal_box(prop, session)
                if ans in ('y', 'a'):
                    run_proposal(prop, session, auto=(ans == 'a'))
        return

    # Interactive loop
    while True:
        try:
            files_n = len(session.ctx.files)
            msgs_n  = len(session.messages)
            staged_n = len(session.ctx.pending)
            parts_p  = []
            if files_n:  parts_p.append(f"{files_n}f")
            if msgs_n:   parts_p.append(f"{msgs_n}m")
            if staged_n: parts_p.append(accent(f"{staged_n}★"))
            ctx_str  = steel("[" + "·".join(parts_p) + "]") if parts_p else ""
            prompt   = f"\n{cyan('you')}{(' ' + ctx_str) if ctx_str else ''} {dim('›')} " if IS_TTY else "you › "
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{dim('  Claw Batto v2 out. 👋')}")
            break

        if not user_input: continue
        if user_input.lower() in ("exit","quit","q","bye"):
            print(dim("  Claw Batto v2 out. 👋"))
            break

        if user_input.startswith("/"):
            handle_slash(user_input, session)
            continue

        # ── Dismiss stale pending proposal if user typed something else ───
        if session.pending_proposal:
            session.pending_proposal = None

        # ── Normal chat / command flow ────────────────────────────────────
        msg = session.build_user_msg(user_input)
        session.add("user", msg)
        resp = ollama_chat(session.messages)
        if not resp: continue

        session.last_response = resp
        session.add("assistant", resp)

        # ── Check for structured PLAN blocks ─────────────────────────────
        proposal = parse_plan_block(resp)
        if not proposal:
            # Build proposal from inline WRITE/RUN markers
            write_targets = parse_write_targets_from_response(resp)
            run_targets   = parse_run_targets_from_response(resp)
            if write_targets or run_targets:
                steps = []
                for i, w in enumerate(write_targets, 1):
                    steps.append(ProposalStep(i, f"Write {Path(w['path']).name}",
                                              "WRITE", w["path"], w.get("content", "")))
                off = len(write_targets)
                for i, r in enumerate(run_targets, off + 1):
                    steps.append(ProposalStep(i, r["desc"], "RUN", r["cmd"], ""))
                if steps:
                    proposal = Proposal("Suggested changes", steps)

        if proposal:
            # Show dialogue box immediately — no waiting for "ok"
            ans = show_proposal_box(proposal, session)
            if ans == 'a':
                session.auto_approve = True
                run_proposal(proposal, session, auto=True)
            elif ans == 'y':
                run_proposal(proposal, session, auto=False)
            else:
                info("Skipped. Changes not applied.")
            continue

        # ── Fallback: capture code blocks ─────────────────────────────────
        blocks = extract_code_blocks(resp)
        if blocks:
            session.last_code = blocks[-1]["content"]
            # Show a mini write dialogue if there's captured code
            ans = ask_action(
                "Code captured — write to file?",
                [f"  {cyan('◆')} {dim(str(len(session.last_code)))} chars  ·  "
                 f"{white('/write <path>')} to stage, {white('/diff')} to preview"],
                auto=False
            )
            if ans in ('y', 'a'):
                path_inp = input(f"  {cyan('File path:')} ").strip()
                if path_inp:
                    session.ctx.stage(path_inp, session.last_code)
                    info(f"Staged. Run /diff to review then /apply to write.")

# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=f"Claw Batto v{CLAW_VERSION} — Baza Dev CLI Agent")
    ap.add_argument("task",            nargs="?",  default="",  help="One-shot task")
    ap.add_argument("-f","--file",     default="", help="Load file into context")
    ap.add_argument("-d","--dir",      default="", help="Load directory into context")
    ap.add_argument("-m","--model",    default="", help="Ollama model override")
    ap.add_argument("--composer",      action="store_true", help="Composer (step-by-step build) mode")
    ap.add_argument("--watch",         default="", help="Watch a file and review on each save")
    ap.add_argument("--doctor",        action="store_true", help="Health check all Baza services")
    ap.add_argument("--plan",          action="store_true", help="Auto-generate a plan for the task")
    args = ap.parse_args()

    if args.model: CLAW_MODEL = args.model

    # claw --doctor  (shortcut: no REPL needed)
    if args.doctor:
        repl(doctor=True)
        sys.exit(0)

    repl(initial_task=args.task, initial_file=args.file,
         initial_dir=args.dir, composer=args.composer,
         watch=args.watch, auto_plan=args.plan)
