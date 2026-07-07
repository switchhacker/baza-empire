"""
Baza Empire — Base Agent
--------------------------
All agents inherit from this. It wires together:
  - Persistent context (memory, identity, empire knowledge, summaries)
  - Skills engine (parse ##SKILL:## calls in LLM output, execute them)
  - Ollama LLM with pooled GPU access
  - Conversation history (per-agent, per-chat)
  - Auto-summarization (every N messages, compress history to a summary)
  - Task journal (every action logged)

Usage:
    class ClawBatto(BaseAgent):
        AGENT_ID = "claw_batto"
        MODEL = "qwen2.5:14b"
        TOKEN_ENV = "TELEGRAM_CLAW_BATTO"

    agent = ClawBatto()
    await agent.run()
"""

import os
import re
import asyncio
import logging
import json
import time
from typing import Optional

# System prompt cache TTL — rebuild from DB every N seconds max
_PROMPT_CACHE_TTL = 120  # 2 minutes

import httpx
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ChatAction

from core.ollama_client import chat_stream_pooled, chat_stream
from core.context_mixin import ContextMixin
from core.memory import (
    init_db, save_message, get_history, get_active_task, set_task, complete_task
)
from core.task_updater import AgentTaskManager
from skills.shared.save_artifact import save_artifact as _save_artifact_fn, save_binary_artifact as _save_binary_artifact_fn
from dashboard.private_inbound import inbound_dir, write_attachment_meta
from core import scaffold_config

logger = logging.getLogger(__name__)

# After this many messages in a session, trigger auto-summarization
AUTO_SUMMARIZE_AFTER = 15

# Max history messages to feed the LLM (keep context window manageable)
MAX_HISTORY = 20

# Cron alert inline-button actions (core/base_agent.py._on_cron_callback) ->
# (suffix appended to the alert text, answer() text on success). Module
# level (not a class attribute) so the handler works against any self with
# an AGENT_ID/tasks pair, not just a real BaseAgent instance.
CRON_CALLBACK_ACTIONS = {
    "ack": "✓ acknowledged",
    "snooze": "😴 snoozed 24h",
    "task": "➕ task created",
}


class BaseAgent(ContextMixin):
    """
    Base class for all Baza Empire agents.
    Subclasses set:
        AGENT_ID  — matches context DB (e.g. "claw_batto")
        MODEL     — Ollama model name (e.g. "qwen2.5:14b")
        TOKEN_ENV — env var name for Telegram bot token
    """
    AGENT_ID: str = "base"
    MODEL: str = "qwen2.5:14b"
    # Local model used when MODEL is rate-limited (cloud quota exhausted, 429,
    # etc). Override per agent in subclass or via agents.yaml `local_fallback_model`.
    FALLBACK_MODEL: str = "qwen2.5:14b"
    TOKEN_ENV: str = ""

    # Graft 5 — per-task model routing. Override in subclass to send code-heavy
    # questions to a coder model, research questions to a long-context model, etc.
    # Keys: code | research | legal | fast | default. Missing keys fall back to MODEL.
    MODEL_ROUTING: dict = {}

    # Set True to use GPU pool (both GPUs shared), False to use AMD only
    USE_GPU_POOL: bool = True

    def __init__(self):
        self.agent_id = self.AGENT_ID
        self.init_context()           # ContextMixin: sets up skills, loads identity
        init_db()                     # Legacy memory/tasks tables
        self._message_counts: dict = {}   # chat_id → message count this session
        self.tasks = AgentTaskManager(self.AGENT_ID)   # local SQLite task manager
        self._prompt_cache: Optional[str] = None
        self._prompt_cache_ts: float = 0.0

        # Event bus for inter-agent communication
        try:
            from core.event_bus import EventBus
            self.event_bus = EventBus(agent_id=self.agent_id)
        except Exception:
            self.event_bus = None

    def save_artifact_binary(self, filename: str, data: bytes, project_id: str = "shared") -> dict:
        """
        Upload a binary file (image, pdf, zip, etc.) to the dashboard artifacts.
        Example:
            with open(path, "rb") as f:
                self.save_artifact_binary("banner.png", f.read(), project_id="proj-ahb123")
        """
        return _save_binary_artifact_fn(
            filename=filename,
            data=data,
            project_id=project_id,
            agent_id=self.AGENT_ID,
        )

    def save_artifact(self, filename: str, content: str, project_id: str = "shared", task_id: str = "") -> dict:
        """
        Save any file (html/json/py/md/sh/yaml/csv/etc.) to the dashboard artifacts.
        Agents call this to persist deliverables Serge can view and download.

        Example:
            self.save_artifact("report.html", "<html>...</html>", project_id="proj-ahb123")
            self.save_artifact("config.json", json.dumps(data), project_id="proj-baza-empire")
            self.save_artifact("setup.py", code_str)
        """
        return _save_artifact_fn(
            filename=filename,
            content=content,
            project_id=project_id,
            agent_id=self.AGENT_ID,
            task_id=task_id,
        )

    # ── System Prompt ─────────────────────────────────────────────────────────

    def build_system_prompt(self, extra: str = "") -> str:
        """
        Full system prompt = base identity + live context injection + task state.
        Cached for _PROMPT_CACHE_TTL seconds to avoid hitting DB on every message.
        Pass extra="" for cached version; non-empty extra forces a fresh build.
        """
        now = time.time()
        if not extra and self._prompt_cache and (now - self._prompt_cache_ts) < _PROMPT_CACHE_TTL:
            return self._prompt_cache

        prompt = self.get_system_prompt()  # from ContextMixin
        # Always inject current task state so agent knows what to work on
        try:
            task_summary = self.tasks.summary_text()
            prompt += f"\n\n== YOUR CURRENT TASKS (live from local DB) ==\n{task_summary}\n== END TASKS ==\n"
            prompt += (
                "\n\nCRITICAL: When you complete a task, you MUST call the update_task skill with status=completed. "
                "When you start working on something, call update_task with status=in_progress. "
                "Use the task ID shown in brackets above (e.g. [a1b2c3d4]). "
                "This is how your work gets tracked. Do not skip this."
            )
        except Exception:
            pass

        # Inject web search + scraping capability docs
        prompt += (
            "\n\n== WEB TOOLS ==\n"
            "##SKILL:web_search{\"query\": \"...\", \"n\": 5}## → Ollama web search, returns title/url/snippet results\n"
            "##SKILL:web_fetch{\"url\": \"...\", \"max_chars\": 8000}## → fetch rendered page content (Phantom Browser)\n"
            "##SKILL:browse{\"action\": \"goto\", \"url\": \"...\"}## → interactive browser session (then read/click/type by index)\n"
            "##SKILL:scrape_page{\"url\": \"...\", \"max_chars\": 4000}## → lightweight HTML scrape (no API key needed)\n"
            "Workflow: use web_search to find URLs, then web_fetch or scrape_page to read them.\n"
            "Always cite URLs when using web data.\n"
            "== END WEB TOOLS =="
        )

        # Inject artifact creation capability docs
        prompt += (
            "\n\n== SAVING ARTIFACTS — MANDATORY ==\n"
            "ANY output you produce that is a file, report, image, document, code, config, or data "
            "MUST be saved as an artifact immediately. Do not just describe it in chat.\n"
            "Save with: ##SKILL:artifact_save{\"filename\":\"name.ext\",\"content\":\"...\",\"project_id\":\"proj-id\"}##\n"
            "Supported: any text-based format (.md .py .sh .js .ts .html .json .yaml .csv .sql .txt .log .svg .xml .toml .rst .rb .go .php .css and more)\n"
            "Project IDs: proj-ahb123 (All Home Building Co), proj-baza-empire (agents/infra), shared (general)\n"
            "Examples:\n"
            "  ##SKILL:artifact_save{\"filename\":\"proposal.md\",\"content\":\"# Proposal\\n...\",\"project_id\":\"proj-ahb123\"}##\n"
            "  ##SKILL:artifact_save{\"filename\":\"config.json\",\"content\":\"{}\",\"project_id\":\"proj-baza-empire\"}##\n"
            "Rules:\n"
            "  - If you write code → save it as .py/.js/.sh/.yaml\n"
            "  - If you write a report/plan/analysis → save it as .md\n"
            "  - If you produce structured data → save it as .json or .csv\n"
            "  - If you produce HTML → save it as .html\n"
            "  - Save FIRST, then send a brief summary in chat.\n"
            "  - There is NO limit on how much you save. Save everything. More is better.\n"
            "  - If a task produces 10 files, save all 10. Do not summarize or truncate.\n"
            "  - When saving artifacts: use full markdown, headers, and code blocks — that's what artifacts are for.\n"
            "== END ARTIFACTS ==\n"
            "\n\n== RESEARCH → PLAN → EXECUTE WORKFLOW ==\n"
            "For any task that takes more than 2 steps, follow this sequence:\n"
            "PHASE 1 — RESEARCH: Use web_search + scrape_page to gather what you need. Save raw notes:\n"
            "  ##SKILL:artifact_save{\"filename\":\"research_[topic].md\",\"content\":\"...\",\"project_id\":\"...\"}##\n"
            "PHASE 2 — PLAN: Write a numbered execution plan. SAVE IT FIRST before executing anything:\n"
            "  ##SKILL:artifact_save{\"filename\":\"plan_[topic].md\",\"content\":\"# Plan\\n1. ...\",\"project_id\":\"...\"}##\n"
            "PHASE 3 — EXECUTE: Execute each step. Save each output file immediately after generating it.\n"
            "PHASE 4 — REPORT: Save a summary when done:\n"
            "  ##SKILL:artifact_save{\"filename\":\"report_[topic].md\",\"content\":\"# Results\\n...\",\"project_id\":\"...\"}##\n"
            "  Then send a brief chat summary listing what was saved.\n"
            "RULE: Never describe a plan without saving it. Never finish work without saving results.\n"
            "== END WORKFLOW ==\n"
            "\n\n== ARTIFACT AWARENESS ==\n"
            "Other agents save files to shared artifacts. Check what exists before duplicating work.\n"
            "To list recent artifacts: ##SKILL:list_artifacts{\"limit\":20}##\n"
            "To list another agent's work: ##SKILL:list_artifacts{\"agent_id\":\"sam_axe\",\"limit\":10}##\n"
            "To list by project: ##SKILL:list_artifacts{\"project_id\":\"proj-ahb123\",\"limit\":15}##\n"
            "When referencing another agent's work (e.g. regenerate Sam's avatar image), call list_artifacts first to find the exact filename.\n"
            "== END ARTIFACT AWARENESS =="
        )

        # Inject dynamic skill creation docs
        prompt += (
            "\n\n== DYNAMIC TOOLS — CREATE ANY SKILL YOU NEED ==\n"
            "If you need a tool that doesn't exist, CREATE IT on the spot:\n"
            "##SKILL:create_skill{\"name\":\"tool_name\",\"description\":\"what it does\","
            "\"code\":\"#!/usr/bin/env python3\\nimport os,json\\nargs=json.loads(os.environ.get('SKILL_ARGS','{}'))\\n# your code\\nprint(result)\"}##\n"
            "Rules: name must be snake_case. Code runs as subprocess. Read args from SKILL_ARGS env var. Print result to stdout.\n"
            "You can create: API callers, system queries, file processors, calculators, scrapers — anything.\n"
            "After creating, immediately call it: ##SKILL:tool_name{\"arg\":\"value\"}##\n"
            "== END DYNAMIC TOOLS =="
        )

        # Inject AHB123 business data access
        prompt += (
            "\n\n== AHB123 BUSINESS DATA ==\n"
            "You have access to AHBCO LLC business data via the ahb123_query skill:\n"
            "##SKILL:ahb123_query{\"action\":\"list_clients\",\"filters\":{\"status\":\"active\"}}##\n"
            "##SKILL:ahb123_query{\"action\":\"list_projects\",\"filters\":{\"status\":\"in-progress\"}}##\n"
            "##SKILL:ahb123_query{\"action\":\"list_invoices\",\"filters\":{\"status\":\"overdue\"}}##\n"
            "##SKILL:ahb123_query{\"action\":\"dashboard_stats\"}## — get summary counts\n"
            "##SKILL:ahb123_query{\"action\":\"search\",\"filters\":{\"q\":\"keyword\"}}## — search all tables\n"
            "Write actions: add_client, update_client, add_project, update_project, add_invoice, add_receipt, add_payroll, add_estimate\n"
            "All data is for All Home Building Co LLC (AHBCO), Philadelphia PA.\n"
            "Dashboard: http://localhost:8888/ahb123\n"
            "== END AHB123 =="
        )

        if extra:
            prompt += f"\n\n{extra}"
        else:
            # Only cache the no-extra version
            self._prompt_cache = prompt
            self._prompt_cache_ts = time.time()
        return prompt

    def web_search(self, query: str, n: int = 5) -> list:
        """
        Search the web via Ollama API (falls back to DuckDuckGo if no API key).
        Returns list of {title, url, snippet}.
        Example: results = self.web_search("PA HIC license renewal 2025")
        """
        result = self.skills.run("web_search", {"query": query, "n": n, "output": "json"})
        if result.get("success"):
            try:
                import json as _json
                data = _json.loads(result.get("output", "{}"))
                return data.get("results", [])
            except Exception:
                pass
        return []

    def web_fetch(self, url: str, max_chars: int = 8000) -> dict:
        """
        Fetch full page content via Ollama's web fetch API.
        Returns {success, title, content, links, url}.
        Example: page = self.web_fetch("https://www.phila.gov/permits/")
        """
        result = self.skills.run("web_fetch", {"url": url, "max_chars": max_chars, "output": "json"})
        if result.get("success"):
            try:
                import json as _json
                return _json.loads(result.get("output", "{}"))
            except Exception:
                pass
        return {"success": False, "error": result.get("output", "skill error")}

    def scrape_page(self, url: str, max_chars: int = 4000) -> dict:
        """
        Fetch and extract clean text from a URL.
        Example: page = self.scrape_page("https://www.attorneygeneral.gov/...")
        Returns: {success, title, text, url, chars}
        """
        result = self.skills.run("scrape_page", {"url": url, "max_chars": max_chars, "output": "json"})
        if result.get("success"):
            try:
                import json as _json
                return _json.loads(result.get("output", "{}"))
            except Exception:
                pass
        return {"success": False, "error": result.get("output", "skill error")}

    # ── Approval Gate (Graft 2) ───────────────────────────────────────────────

    def request_approval(self, action: str, details: str = "",
                         category: str = "general", timeout: int = None) -> bool:
        """
        Block on Serge's approval before doing something destructive or expensive.
        Wraps core.approval.request_approval, passing the agent's own bot token.

        Returns True if approved, False if denied or timed out. Always journaled.

        Use for: deletes, sends, spends, external API mutations, anything you'd
        regret doing wrong. Don't use for read-only or generative work.
        """
        from core.approval import request_approval as _ra
        return _ra(self.AGENT_ID, action, details=details,
                   category=category, timeout=timeout)

    # ── LLM Call ──────────────────────────────────────────────────────────────

    # Graft 5 — naive keyword scorer for per-task model routing.
    # First match wins. Order matters: more specific tasks should come first.
    _ROUTING_KEYWORDS = (
        ("code",     ("code", "debug", "fix bug", "stack trace", "deploy", "script",
                      "function", "compile", "regex", "syntax", "git ", "docker",
                      "kubernetes", "ansible", "terraform", "systemd")),
        ("legal",    ("legal", "lawsuit", "contract", "liability", "compliance",
                      "tax", "irs", "deduction", "schedule c", "1099", "w-9",
                      "llc", "operating agreement", "lien", "invoice", "audit")),
        ("research", ("research", "investigate", "find out", "look up", "market",
                      "competitor", "news", "industry", "trend", "benchmark",
                      "compare", "analysis", "report on")),
        ("fast",     ("quick", "tldr", "brief", "short", "summary", "summarize",
                      "in one sentence", "yes or no", "real quick")),
    )

    def _route_model(self, text: str) -> str:
        """Pick a model name based on the user message keywords.
        Returns self.MODEL if no MODEL_ROUTING is configured or no keyword matches.
        """
        if not self.MODEL_ROUTING:
            return self.MODEL
        if not text:
            return self.MODEL_ROUTING.get("default", self.MODEL)
        low = text.lower()
        for task, keywords in self._ROUTING_KEYWORDS:
            if task not in self.MODEL_ROUTING:
                continue
            if any(k in low for k in keywords):
                return self.MODEL_ROUTING[task]
        return self.MODEL_ROUTING.get("default", self.MODEL)

    def llm_chat(self, messages: list, system_prompt: str,
                 model_override: str = None) -> str:
        """
        Run an LLM inference. Streams internally, returns full response string.
        Uses GPU pool if USE_GPU_POOL, otherwise AMD only.
        Logs token usage to agent_usage table.

        If `model_override` is provided, it bypasses self.MODEL — use this with
        `_route_model()` for per-task routing.

        Runs every input through core.context_optimizer first, which dedupes
        history, compresses old turns into a summary, hard-caps tokens, and
        may downgrade trivial requests to a smaller model. Set the env var
        BAZA_OPTIMIZER_OFF=1 to disable per-agent.
        """
        model = model_override or self.MODEL

        # ── Context optimizer (token saver) ──────────────────────────────────
        if not os.environ.get("BAZA_OPTIMIZER_OFF"):
            try:
                from core.context_optimizer import optimize
                opt_msgs, opt_sys, opt_model, stats = optimize(
                    agent_id=self.AGENT_ID,
                    messages=messages,
                    system_prompt=system_prompt,
                    target_model=model,
                    max_tokens=int(os.environ.get("BAZA_OPTIMIZER_MAX_TOKENS", "6000")),
                    keep_recent=int(os.environ.get("BAZA_OPTIMIZER_KEEP_RECENT", "8")),
                )
                # Only adopt the optimizer's suggestions if they actually saved tokens
                if stats["after_tokens"] < stats["before_tokens"]:
                    messages = opt_msgs
                    system_prompt = opt_sys
                if stats["downgraded"]:
                    logger.info(f"[{self.AGENT_ID}] optimizer downgraded {model} → {opt_model} ({stats['complexity']})")
                    model = opt_model
                if stats["saved_pct"] >= 20:
                    logger.info(f"[{self.AGENT_ID}] optimizer saved {stats['saved_pct']}% ({stats['before_tokens']}→{stats['after_tokens']} tokens)")
            except Exception as e:
                logger.warning(f"[{self.AGENT_ID}] optimizer failed (using raw messages): {e}")

        full = ""
        usage_meta = {}

        def _on_complete(meta):
            nonlocal usage_meta
            usage_meta = meta

        if self.USE_GPU_POOL:
            for chunk in chat_stream_pooled(model, messages, system_prompt,
                                            self.AGENT_ID, on_complete=_on_complete,
                                            fallback_model=self.FALLBACK_MODEL):
                full += chunk
        else:
            from core.ollama_client import OLLAMA_AMD_URL
            for chunk in chat_stream(model, messages, system_prompt,
                                     OLLAMA_AMD_URL, on_complete=_on_complete):
                full += chunk

        if usage_meta:
            try:
                from core.context_db import usage_log
                duration_ms = usage_meta.get("total_duration_ns", 0) // 1_000_000
                usage_log(
                    agent_id=self.AGENT_ID,
                    model=usage_meta.get("model", model),
                    provider=usage_meta.get("provider", "ollama"),
                    prompt_tokens=usage_meta.get("prompt_tokens", 0),
                    completion_tokens=usage_meta.get("completion_tokens", 0),
                    duration_ms=duration_ms
                )
            except Exception:
                pass

        return full.strip()


    # ── Task Command Interception ─────────────────────────────────────────────

    CREATE_PATTERNS = [
        r'create\s+(?:a\s+)?(?:new\s+)?task[:\s]+(.+?)(?:\s+for\s+(\w+))?$',
        r'add\s+(?:a\s+)?(?:new\s+)?task[:\s]+(.+?)(?:\s+for\s+(\w+))?$',
        r'new\s+task[:\s]+(.+?)(?:\s+for\s+(\w+))?$',
        r'(?:simon|claw|sam|phil|rex|duke|scout|nova)[,\s]+create\s+(?:a\s+)?(?:new\s+)?task[:\s]+(.+)',
    ]

    AGENT_NAMES = {
        'simon': 'simon_bately', 'claw': 'claw_batto', 'sam': 'sam_axe',
        'phil': 'phil_hass', 'rex': 'rex_valor', 'duke': 'duke_harmon',
        'scout': 'scout_reeves', 'nova': 'nova_sterling',
    }

    def _try_create_task_from_message(self, text: str) -> str | None:
        """
        Detect "create task: X" or "new task: X" in user message.
        Writes to DB immediately. Returns confirmation string or None.
        """
        import re
        t = text.strip()

        # Pattern: "<agent>, create a new task <title>"
        m = re.match(
            r'(?:simon|claw|sam|phil|rex|duke|scout|nova)[,\s]+create\s+(?:a\s+)?(?:new\s+)?task\s+(.+)',
            t, re.IGNORECASE
        )
        if not m:
            # Pattern: "create task: <title>"
            m = re.match(r'(?:create|add|new)\s+(?:a\s+)?(?:new\s+)?task[:\s]+(.+)', t, re.IGNORECASE)

        if not m:
            return None

        raw_title = m.group(1).strip()

        # Check if another agent is mentioned at the end: "...for claw"
        assign_to = self.AGENT_ID
        assign_m = re.search(r'\s+(?:for|assign(?:ed)?\s+to)\s+(\w+)\s*$', raw_title, re.IGNORECASE)
        if assign_m:
            name_key = assign_m.group(1).lower()
            if name_key in self.AGENT_NAMES:
                assign_to = self.AGENT_NAMES[name_key]
                raw_title = raw_title[:assign_m.start()].strip()

        # Infer project from keywords
        project_id = 'proj-ahb123'
        lower = raw_title.lower()
        if any(k in lower for k in ['baza', 'node', 'firmware', 'edge', 'agent']):
            project_id = 'proj-baza-empire'

        task_id = self.tasks.add(
            project_id=project_id,
            title=raw_title,
            description=raw_title,
            priority='medium',
        )
        # If assigned to someone else, update that field
        if assign_to != self.AGENT_ID:
            from core.task_updater import update_task
            update_task(task_id, {'assigned_to': assign_to})

        if task_id:
            assignee_display = assign_to.replace('_', ' ').title()
            return (
                f"Task created and saved to project board\n"
                f"ID: {task_id[:8]}\n"
                f"Title: {raw_title}\n"
                f"Assigned to: {assignee_display}\n"
                f"Project: {project_id}\n"
                f"Status: pending\n"
                f"Use the dashboard to queue it to an agent, or I can start on it now."
            )
        return None


    # ── Directive Intent Handling ────────────────────────────────────────────

    def _maybe_handle_directive(self, text: str) -> str | None:
        """If `text` parses as a known directive, dispatch and return a
        Telegram-formatted reply. Returns None to fall through to LLM."""
        try:
            from core.intent_router import parse_intent
            from core.intent_dispatcher import dispatch, telegram_format
        except Exception:
            return None
        env = parse_intent(text)
        intent = env.get("intent")
        # Only intercept clearly-known directives. Unknown / empty falls through
        # to the LLM so casual messages like "/me will check" don't get hijacked.
        if intent in ("unknown", None):
            return None
        try:
            out = dispatch(env, extra={"agent_id": self.AGENT_ID})
        except Exception as e:
            return f"directive failed: {e}"
        return telegram_format(out)


    # ── Print Request Handling ───────────────────────────────────────────────

    def _is_print_request(self, text: str) -> bool:
        """Detect print-related requests."""
        t = text.lower().strip()
        print_phrases = [
            "print this", "print that", "print it", "print the",
            "print a test", "print test", "send to printer",
            "print page", "printer status", "print status",
            "print queue", "cancel print", "print last",
            "print image", "print photo", "print file",
            "print invoice", "print contract", "print report",
            "print document", "print pdf",
        ]
        return any(phrase in t for phrase in print_phrases) or t == "print"

    def _find_printable_file(self, text: str) -> str | None:
        """Find the best file to print based on the request text and agent memory."""
        import re as _re, glob as _glob

        # 1. Explicit file path in the message
        path_match = _re.search(r'["\']?(/\S+\.\w{2,5})["\']?', text)
        if path_match and os.path.exists(path_match.group(1)):
            return path_match.group(1)

        # 2. Agent memory — last analyzed photo or generated image
        for key in ["last_analyzed_photo", "last_image_generated"]:
            val = self.recall(key)
            if val and os.path.exists(val):
                return val

        # 3. Extract artifact name from message: "print the invoice" → search for "invoice"
        t = text.lower()
        # Strip common prefixes to get the target noun
        for prefix in ["print the ", "print my ", "print this ", "print that ",
                        "print last ", "print a ", "print "]:
            if t.startswith(prefix):
                target = t[len(prefix):].strip().rstrip(".")
                if target and target not in ("this", "that", "it", "page", "file", "document"):
                    break
        else:
            target = ""

        if target:
            # Search artifacts for matching filename
            base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "dashboard", "artifacts")
            if os.path.isdir(base):
                matches = _glob.glob(os.path.join(base, "**", f"*{target}*"), recursive=True)
                # Filter out .meta files
                matches = [m for m in matches if not m.endswith(".meta") and os.path.isfile(m)]
                if matches:
                    # Return most recently modified
                    return max(matches, key=os.path.getmtime)

        # 4. "print last image/photo" — find most recent image in artifacts
        if any(w in t for w in ["image", "photo", "picture", "last"]):
            base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "dashboard", "artifacts")
            if os.path.isdir(base):
                imgs = []
                for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp"]:
                    imgs.extend(_glob.glob(os.path.join(base, "**", ext), recursive=True))
                imgs = [i for i in imgs if not i.endswith(".meta")]
                if imgs:
                    return max(imgs, key=os.path.getmtime)

        return None

    async def _handle_print_request(self, text: str, chat_id: int) -> str:
        """Handle print requests directly — bypass LLM, run skill."""
        t = text.lower().strip()
        loop = asyncio.get_event_loop()

        # Status / queue
        if "status" in t or "queue" in t:
            result = await loop.run_in_executor(
                None, self.skills.run, "print_document", {"action": "status"}, chat_id
            )
            if result.get("success"):
                try:
                    parsed = json.loads(result["output"].split("\n")[-1])
                    return (f"Printer: {parsed.get('printer', 'HP Smart Tank 5101')}\n"
                            f"Status: {parsed.get('status', 'Unknown')}\n"
                            f"Pending jobs: {parsed.get('pending_jobs', 0)}")
                except Exception:
                    return result.get("output", "Printer checked.")
            return f"Could not check printer: {result.get('error', 'unknown error')}"

        # Cancel
        if "cancel" in t:
            result = await loop.run_in_executor(
                None, self.skills.run, "print_document", {"action": "cancel"}, chat_id
            )
            return "Print jobs cancelled." if result.get("success") else f"Cancel failed: {result.get('error')}"

        # Test page
        if "test" in t:
            result = await loop.run_in_executor(
                None, self.skills.run, "print_document",
                {"text": f"BAZA EMPIRE — PRINT TEST\n\nHP Smart Tank 5101 is online and connected.\n\nPrinted by: {self.AGENT_ID.replace('_',' ').title()}\nFramework: agent-framework-v3",
                 "title": "Baza Empire Test Page"}, chat_id
            )
            if result.get("success"):
                return "Test page sent to printer."
            return f"Print failed: {result.get('error', 'unknown error')}"

        # Find the file to print
        file_to_print = self._find_printable_file(text)
        if file_to_print:
            result = await loop.run_in_executor(
                None, self.skills.run, "print_document",
                {"file_path": file_to_print}, chat_id
            )
            if result.get("success"):
                return f"Sent to printer: {os.path.basename(file_to_print)}"
            return f"Print failed: {result.get('error', 'unknown error')}"

        return "Nothing to print. Send me a photo, generate an image, or specify a filename."

    # ── Message Handling ──────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────────────
    # DocPrep intent detection — shared across ALL agents
    # Any agent can recognize "find/build/list" doc requests and route them to
    # the dashboard's curator + package builder. Phil is mentioned in the reply
    # so the user always knows who's curating.
    # ─────────────────────────────────────────────────────────────────────────

    _DOC_FIND_VERBS = (
        "find", "search", "locate", "where is", "where's", "show me",
        "pull up", "get me", "look up", "lookup", "do we have", "i need",
    )
    _DOC_BUILD_VERBS = (
        "build", "create", "prepare", "prep", "make me", "make a", "draft",
        "generate", "put together", "assemble", "package up",
    )
    _DOC_TYPES = {
        "permit":         ["permit", "building permit", "construction permit", "zoning permit"],
        "coi":            ["coi", "certificate of insurance", "insurance cert", "liability cert"],
        "w9":             ["w9", "w-9", "w 9"],
        "license":        ["license", "contractor license", "hic", "home improvement contractor"],
        "contract":       ["contract", "agreement"],
        "change_order":   ["change order", "change-order"],
        "lien_waiver":    ["lien waiver", "waiver of lien"],
        "estimate":       ["estimate", "quote", "bid"],
        "tax_document":   ["tax doc", "1099", "tax form"],
        "id_document":    ["id document", "drivers license", "driver's license", "passport"],
    }

    def _detect_doc_intent(self, text: str):
        """Return {action, doc_type, query, project_hint} dict or None."""
        import re as _re
        t = (text or "").lower().strip()
        if len(t) < 5:
            return None

        action = None
        # Create-project intent: detect before build/find to take priority on "create" verbs
        create_project_phrases = (
            "create a new project", "create new project", "create a project", "create project",
            "start a new project", "start new project", "start a project",
            "new project from", "new project for", "make a new project",
            "set up a project", "set up a new project", "new job from",
            "scan this for context", "scan this for contexts",  # common in Serge's phrasing
        )
        if any(p in t for p in create_project_phrases):
            action = "create_project"
        elif any(v in t for v in self._DOC_BUILD_VERBS) and any(
            kw in t for kw in ("package", "application", "permit application", "coi request")
        ):
            action = "build"
        elif any(t.startswith(v) or f" {v} " in f" {t} " for v in self._DOC_FIND_VERBS):
            # Only fire find-intent if a doc keyword is also present, otherwise it
            # eats normal questions like "find me on twitter"
            doc_keywords = ("permit","coi","w9","license","contract","change order",
                            "lien","estimate","tax","id doc","insurance","application","document",
                            "permits","contracts","licenses","cois")
            if any(kw in t for kw in doc_keywords):
                action = "find"
        elif (t.startswith(("show all","list all")) or "all permits" in t or "all cois" in t
              or "all licenses" in t or "all contracts" in t):
            action = "list"

        if not action:
            return None

        doc_type = None
        for dt, kws in self._DOC_TYPES.items():
            if any(kw in t for kw in kws):
                doc_type = dt
                break

        project_hint = None
        m = _re.search(
            r"\bfor (?:the )?([a-z0-9 .'\-]+?)(?:\s+(?:project|build|deck|reno|renovation|kitchen|bath|bathroom|job|address|home|house)|[?.,]|$)",
            t,
        )
        if m:
            project_hint = m.group(1).strip()

        return {"action": action, "doc_type": doc_type,
                "query": text.strip(), "project_hint": project_hint}

    async def _handle_doc_intent(self, intent: dict, text: str, chat_id: int):
        """Execute the detected intent against the dashboard API and return a reply.
        Works identically for any agent — the reply mentions Phil so the curator
        attribution stays consistent."""
        import urllib.request as _ur
        import json as _j
        import re as _re

        loop = asyncio.get_event_loop()
        action = intent["action"]
        doc_type = intent.get("doc_type")
        project_hint = intent.get("project_hint")
        am_phil = (self.AGENT_ID == "phil_hass")
        prefix = "" if am_phil else "Phil here (relayed by " + self.AGENT_ID.replace("_"," ").title() + "):\n\n"

        def _api(method, path, body=None):
            url = f"http://localhost:8888{path}"
            data = _j.dumps(body).encode() if body is not None else None
            req = _ur.Request(url, data=data, method=method,
                              headers={"Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=30) as r:
                return _j.loads(r.read())

        try:
            if action == "create_project":
                # Stash the pending intent so the next photo from this chat triggers
                # the full create-project-from-photo flow (see handle_attachment).
                import time as _time
                if not hasattr(self, "_pending_intents"):
                    self._pending_intents = {}
                self._pending_intents[chat_id] = {
                    "action": "create_project", "text": text, "ts": _time.time(),
                }
                return (prefix +
                        "🏗️ Got it — send me photo(s) of the space and I'll:\n\n"
                        "  1. Create a new project (status: Planning)\n"
                        "  2. Analyze the scope from the photo\n"
                        "  3. Run an estimate via AHBCO's pricing tables\n"
                        "  4. Save the estimate as an active quote on the project\n\n"
                        "Add a client name, address, or dimensions in the caption if you have them "
                        "and I'll bake that into the project title and estimate.")

            if action in ("find", "list"):
                query_parts = []
                if doc_type:    query_parts.append(doc_type)
                if project_hint: query_parts.append(project_hint)
                query = " ".join(query_parts) if query_parts else text
                results = await loop.run_in_executor(
                    None, lambda: _api("POST", "/api/ahb/documents/find", {"query": query})
                )
                matches = results.get("matches") or []
                if not matches:
                    return (prefix + f"I checked the document library for \"{query}\" but nothing matched. "
                            "Send me the file directly and I'll curate it on the spot, or open AHB123 → DocPrep "
                            "to browse what we have.")
                lines = [prefix + f"📚 Found {len(matches)} match{'es' if len(matches)>1 else ''} for \"{query}\":\n"]
                icons = {"coi":"🛡","license":"🪪","permit":"📜","contract":"📝","w9":"📋",
                         "change_order":"🔄","invoice":"💰","estimate":"🧮","lien_waiver":"⚖️",
                         "lead_form":"📞","project_photo":"📸","blueprint":"📐","receipt":"🧾",
                         "correspondence":"✉️","id_document":"🪪","tax_document":"💼"}
                for i, m in enumerate(matches[:6], 1):
                    icon = icons.get(m.get("doc_type"), "📄")
                    lines.append(f"{i}. {icon} {(m.get('doc_type') or '').upper()} — {m.get('entity') or 'unknown'}")
                    if m.get("doc_date"):
                        lines.append(f"   Date: {m['doc_date']}")
                    if m.get("summary"):
                        lines.append(f"   {m['summary'][:160]}")
                    lines.append(f"   Open: http://localhost:8888/api/ahb/documents/file/{m['id']}")
                    lines.append("")
                lines.append("View the full library + edit fields in AHB123 → DocPrep tab.")
                return "\n".join(lines)

            if action == "build":
                if not project_hint:
                    return (prefix + "I need to know which project. Try: \"Build a permit package "
                            "for the Warrington deck build\" or \"create a COI request for Kim French project\".")
                projects = await loop.run_in_executor(None, lambda: _api("GET", "/api/ahb/projects"))
                hint = project_hint.lower()
                hint_words = set(_re.findall(r"\w+", hint))
                best = None
                best_score = 0
                for p in projects:
                    blob = " ".join(filter(None, [
                        p.get("title",""), p.get("client_name",""),
                        p.get("address",""), (p.get("description") or "")[:200],
                    ])).lower()
                    score = sum(1 for w in hint_words if w in blob and len(w) > 2)
                    if score > best_score:
                        best_score = score
                        best = p
                if not best or best_score == 0:
                    sample = ", ".join((p.get("title") or "")[:30] for p in projects[:5])
                    return (prefix + f"Couldn't find a project matching \"{project_hint}\". "
                            f"A few I see: {sample}. Try the exact title or address.")
                pkg_type = doc_type or "permit"
                if pkg_type not in ("permit", "coi_request", "contract", "change_order"):
                    pkg_type = "permit"
                result = await loop.run_in_executor(
                    None, lambda: _api("POST", "/api/ahb/packages/build-from-project",
                                       {"project_id": best["id"], "package_type": pkg_type})
                )
                if not result.get("success"):
                    return prefix + f"Build failed: {result.get('error','unknown')}"
                pkg_id = result.get("id")
                return (prefix +
                    f"📦 Built {pkg_type.replace('_',' ').upper()} package for "
                    f"\"{best.get('title','project')}\".\n\n"
                    f"• {result.get('standing_docs',0)} standing AHBCO docs attached "
                    f"(license, COI, W9)\n"
                    f"• {result.get('project_docs',0)} project-specific docs attached\n"
                    f"• Form prefilled with project + client info\n\n"
                    f"📄 View the package PDF: http://localhost:8888/api/ahb/packages/{pkg_id}/pdf\n"
                    f"⬇ Download: http://localhost:8888/api/ahb/packages/{pkg_id}/pdf?download=1\n"
                    f"✏️ Edit in dashboard: AHB123 → DocPrep → click the new package")
        except Exception as e:
            logger.error(f"[{self.AGENT_ID}] doc intent failed: {e}")
            return prefix + f"Hit an error handling that request: {e}"
        return None

    async def handle_attachment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Capture every photo/document/video/audio sent in chat → save to datahub.

        Files are saved under dashboard/artifacts/<agent_id>-uploads/ with a sidecar
        .meta file tagging them with the agent_id so the Data Hub can attribute them
        correctly when filtering 'by agent'."""
        import datetime as _dt
        chat_id = update.effective_chat.id
        msg = update.message
        if not msg:
            return
        try:
            framework_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            # Inbound is public now — files land in Data Hub by default.
            # `inbound_dir()` returns the same on-disk path as the legacy
            # `private_inbound_dir()` (dotted name kept for vision.db
            # backwards-compat) but the semantics are public.
            upload_dir = inbound_dir(framework_dir, self.AGENT_ID)

            # Pick the right file object + filename
            file_obj = None
            orig_name = None
            kind = "file"
            caption = (msg.caption or "").strip()
            media_group_id = getattr(msg, "media_group_id", None)
            logger.info(f"[{self.AGENT_ID}] attachment caption={caption!r} "
                        f"media_group_id={media_group_id}")

            if msg.photo:
                file_obj = await context.bot.get_file(msg.photo[-1].file_id)
                orig_name = f"photo_{msg.photo[-1].file_unique_id}.jpg"
                kind = "photo"
            elif msg.document:
                file_obj = await context.bot.get_file(msg.document.file_id)
                orig_name = msg.document.file_name or f"doc_{msg.document.file_unique_id}"
                kind = "document"
            elif msg.video:
                file_obj = await context.bot.get_file(msg.video.file_id)
                orig_name = msg.video.file_name or f"video_{msg.video.file_unique_id}.mp4"
                kind = "video"
            elif msg.audio:
                file_obj = await context.bot.get_file(msg.audio.file_id)
                orig_name = msg.audio.file_name or f"audio_{msg.audio.file_unique_id}.mp3"
                kind = "audio"
            elif msg.voice:
                file_obj = await context.bot.get_file(msg.voice.file_id)
                orig_name = f"voice_{msg.voice.file_unique_id}.ogg"
                kind = "voice"

            if not file_obj:
                return

            # Sanitize + timestamp the saved filename so duplicates don't collide
            import re as _re
            safe = _re.sub(r"[^\w.\-_ ()]", "_", orig_name).strip() or "upload"
            ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"{ts}_{safe}"
            fpath = os.path.join(upload_dir, fname)
            await file_obj.download_to_drive(fpath)

            # Sidecar meta — JSON, attachment metadata only. No private flag:
            # inbound is public, and the file appears in Data Hub. The user
            # can explicitly send specific files to the vault from the UI.
            write_attachment_meta(fpath, extra={
                "agent_id": self.AGENT_ID,
                "chat_id": chat_id,
                "kind": kind,
                "received_at": _dt.datetime.now().isoformat(),
                "caption": caption[:500] if caption else "",
            })
            if kind == "photo":
                from dashboard.private_inbound import observe_into_vision
                observe_into_vision(fpath, agent_id=self.AGENT_ID)

            logger.info(f"[{self.AGENT_ID}] saved {kind} from chat {chat_id}: {fname}")
            self.journal("attachment_received",
                         f"{kind}: {orig_name} ({os.path.getsize(fpath)} bytes)",
                         chat_id=chat_id)

            # Save a chat record so history shows the upload happened
            try:
                save_message(chat_id, self.AGENT_ID, "user",
                             f"[{kind} uploaded: {orig_name}]" + (f"  caption: {caption}" if caption else ""))
            except Exception:
                pass

            # Track recent photo so the create-project flow can pair text+photo order-independently
            import time as _time
            if kind == "photo":
                if not hasattr(self, "_recent_photos"):
                    self._recent_photos = {}
                self._recent_photos[chat_id] = {"fpath": fpath, "ts": _time.time(), "caption": caption}
                try:
                    self.set_memory("last_uploaded_photo", fpath, category="recent_files")
                except Exception:
                    pass

            # Check if this attachment should trigger the create-project flow.
            # Four signals, in priority order:
            #   (a) caption on THIS photo has create-project intent
            #   (b) this photo is part of a media group whose first photo already
            #       triggered the flow (link to same project)
            #   (c) pending intent stored from a recent standalone text message
            #   (d) chat history contains a create-project message within last 3 min
            is_create_project = False
            link_to_existing_project = None  # if media group follow-up
            pending_text = ""
            detection_reason = "none"

            if not hasattr(self, "_media_groups"):
                self._media_groups = {}  # {media_group_id: {project_id, ts, chat_id}}

            if kind == "photo":
                # (a) caption intent
                if caption:
                    cap_intent = self._detect_doc_intent(caption)
                    if cap_intent and cap_intent.get("action") == "create_project":
                        is_create_project = True
                        pending_text = caption
                        detection_reason = "caption"
                # (b) media group follow-up
                if not is_create_project and media_group_id:
                    mg = self._media_groups.get(media_group_id)
                    if mg and (_time.time() - mg.get("ts", 0)) < 300:
                        link_to_existing_project = mg.get("project_id")
                        detection_reason = "media_group_followup"
                # (c) pending intent
                if not is_create_project and not link_to_existing_project \
                        and hasattr(self, "_pending_intents"):
                    pending = self._pending_intents.get(chat_id)
                    if pending and pending.get("action") == "create_project" \
                            and (_time.time() - pending.get("ts", 0)) < 600:
                        is_create_project = True
                        pending_text = pending.get("text", "")
                        detection_reason = "pending_intent"
                # (d) chat history fallback — last 3 min
                if not is_create_project and not link_to_existing_project:
                    try:
                        from core.memory import get_history
                        recent = get_history(chat_id, self.AGENT_ID, limit=10) or []
                        for h in reversed(recent):
                            if h.get("role") != "user":
                                continue
                            content = h.get("content") or ""
                            hi = self._detect_doc_intent(content)
                            if hi and hi.get("action") == "create_project":
                                is_create_project = True
                                pending_text = content
                                detection_reason = "chat_history_scan"
                                break
                    except Exception as e:
                        logger.debug(f"[{self.AGENT_ID}] history scan failed: {e}")

            logger.info(f"[{self.AGENT_ID}] attach-branch kind={kind} "
                        f"create_project={is_create_project} link_to={link_to_existing_project} "
                        f"reason={detection_reason} caption_present={bool(caption)}")

            # Branch A: follow-up photo in a media group whose first photo already created a project
            if link_to_existing_project:
                try:
                    async def _link_photo():
                        import urllib.request as _ur, json as _j
                        loop2 = asyncio.get_event_loop()
                        def _api(method, path, body=None):
                            url = f"http://localhost:8888{path}"
                            data = _j.dumps(body).encode() if body is not None else None
                            req = _ur.Request(url, data=data, method=method,
                                              headers={"Content-Type": "application/json"})
                            with _ur.urlopen(req, timeout=30) as r:
                                return _j.loads(r.read())
                        try:
                            await loop2.run_in_executor(None, lambda: _api("POST", "/api/ahb/files", {
                                "name": os.path.basename(fpath),
                                "file_type": "image",
                                "file_path": fpath,
                                "project_id": link_to_existing_project,
                                "category": "project_photo",
                                "tags": f"{self.AGENT_ID},auto,media_group",
                            }))
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"📎 Linked additional photo to project <code>{link_to_existing_project}</code>",
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.warning(f"[{self.AGENT_ID}] link follow-up photo failed: {e}")
                    asyncio.ensure_future(_link_photo())
                except Exception as e:
                    logger.error(f"[{self.AGENT_ID}] media-group follow-up failed: {e}")
                return

            # Branch B: create project flow (curator + project + estimate all inside)
            if is_create_project:
                # Clear pending so the next photo doesn't re-create the project
                if hasattr(self, "_pending_intents"):
                    self._pending_intents.pop(chat_id, None)
                init_ack = f"\u2705 Got your {kind}: {fname}\n🏗️ Creating project + running estimator..."
                try:
                    await context.bot.send_message(chat_id=chat_id, text=init_ack)
                except Exception:
                    pass

                # Record this media group so subsequent photos in the album link to the new project
                async def _flow():
                    try:
                        pid = await self._create_project_from_photo(
                            fpath=fpath, caption=pending_text or caption, chat_id=chat_id,
                            context=context, kind=kind
                        )
                        if pid and media_group_id:
                            self._media_groups[media_group_id] = {
                                "project_id": pid, "ts": _time.time(), "chat_id": chat_id,
                            }
                    except Exception as e:
                        logger.error(f"[{self.AGENT_ID}] create-project flow crashed: {e}")
                asyncio.ensure_future(_flow())
                return

            # Branch C: print request — user sent a photo/doc with "print this" caption
            if caption and self._is_print_request(caption):
                try:
                    await context.bot.send_message(chat_id=chat_id,
                        text=f"🖨️ Sending {fname} to printer...")
                except Exception:
                    pass
                async def _print_and_reply():
                    try:
                        loop2 = asyncio.get_event_loop()
                        # Build print options from caption
                        opts = {"file_path": fpath}
                        cap_lower = caption.lower()
                        if "fit to page" in cap_lower or "fit-to-page" in cap_lower:
                            opts["fit_to_page"] = True
                        if "landscape" in cap_lower:
                            opts["orientation"] = "landscape"
                        if "color" in cap_lower:
                            opts["color"] = True
                        if "duplex" in cap_lower or "double" in cap_lower or "both sides" in cap_lower:
                            opts["duplex"] = True
                        result = await loop2.run_in_executor(
                            None, self.skills.run, "print_document", opts, chat_id
                        )
                        if result.get("success"):
                            reply = f"✅ Printed: {os.path.basename(fpath)}"
                        else:
                            reply = f"❌ Print failed: {result.get('error', 'unknown error')}"
                        await context.bot.send_message(chat_id=chat_id, text=reply)
                    except Exception as e:
                        logger.error(f"[{self.AGENT_ID}] print-from-attachment failed: {e}")
                        try:
                            await context.bot.send_message(chat_id=chat_id,
                                text=f"❌ Print error: {e}")
                        except Exception:
                            pass
                asyncio.ensure_future(_print_and_reply())
                return

            # Send a fast initial ack so the user knows it landed
            init_ack = f"\u2705 Got your {kind}: {fname}\n\u2026 analyzing with Phil's curator..."
            try:
                ack_msg = await context.bot.send_message(chat_id=chat_id, text=init_ack)
            except Exception:
                ack_msg = None

            # Run the curator skill in the background — doesn't block the chat handler
            async def _curate_and_reply():
                try:
                    loop2 = asyncio.get_event_loop()
                    result = await loop2.run_in_executor(
                        None, self.skills.run, "curate_document",
                        {"file_path": fpath, "agent_id": self.AGENT_ID, "chat_id": chat_id}
                    )
                    # Skills engine returns the stdout — parse it
                    try:
                        analysis = json.loads(result) if isinstance(result, str) else result
                    except Exception:
                        analysis = {}
                    if not isinstance(analysis, dict):
                        analysis = {}
                    doc_type = analysis.get("doc_type") or "document"
                    entity   = analysis.get("entity") or "unknown entity"
                    summary  = (analysis.get("summary") or "").strip()
                    relevance= (analysis.get("relevance") or "").strip()
                    suggested= analysis.get("suggested_name") or fname
                    tags     = analysis.get("tags") or []
                    msg_text = (
                        f"\u2705 Filed: <b>{doc_type.upper()}</b>\n"
                        f"<b>Entity:</b> {entity}\n"
                    )
                    if analysis.get("doc_date"):
                        msg_text += f"<b>Date:</b> {analysis['doc_date']}\n"
                    if summary:
                        msg_text += f"\n{summary}\n"
                    if relevance:
                        msg_text += f"\n<i>{relevance}</i>\n"
                    if tags:
                        msg_text += f"\n<code>tags: {', '.join(tags[:6])}</code>\n"
                    msg_text += f"\n\U0001F4C1 <code>{suggested}</code>"
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")
                    except Exception:
                        await context.bot.send_message(chat_id=chat_id, text=msg_text.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","").replace("<code>","").replace("</code>",""))

                    # Post-curate filing: receipts → ahb_receipts, permits/COIs/etc → link to project.
                    # Mirrors the legacy core/agent.py pipeline so every BaseAgent-based bot
                    # (Specter, Phil, Nova, ...) actually files docs instead of just curating them.
                    try:
                        file_result = await loop2.run_in_executor(
                            None, self.skills.run, "file_document",
                            {
                                "file_path": fpath,
                                "analysis": analysis,
                                "caption": caption,
                                "agent_id": self.AGENT_ID,
                            },
                        )
                        try:
                            filed = json.loads(file_result) if isinstance(file_result, str) else (file_result or {})
                        except Exception:
                            filed = {}
                        if not isinstance(filed, dict):
                            filed = {}
                        action = filed.get("action")
                        reply = None
                        if action == "queued_in_quickrf":
                            total = filed.get("total") or 0
                            vendor = filed.get("vendor") or "unknown vendor"
                            rdate = filed.get("receipt_date") or "no date"
                            proj = filed.get("project_id") or "-"
                            cat = filed.get("category") or "(needs category)"
                            reply = (
                                f"🅿️ Parked in QuickRF — review & confirm\n"
                                f"Vendor: {vendor}\n"
                                f"Date: {rdate}\n"
                                f"Total: ${float(total or 0):.2f}\n"
                                f"Category: {cat}\n"
                                f"Project: {proj}\n"
                                f"Open AHB123 → QuickRF to confirm or edit."
                            )
                        elif action == "filed_receipt":
                            # Legacy path kept for any non-Telegram callers
                            total = filed.get("total") or 0
                            vendor = filed.get("vendor") or "unknown vendor"
                            rdate = filed.get("receipt_date") or "no date"
                            proj = filed.get("project_id") or "-"
                            reply = (
                                f"🧾 Receipt filed in AHB123\n"
                                f"Vendor: {vendor}\n"
                                f"Date: {rdate}\n"
                                f"Total: ${float(total or 0):.2f}\n"
                                f"Project: {proj}\n"
                                f"ID: {filed.get('receipt_id','')}"
                            )
                        elif action == "linked_to_project":
                            reply = (
                                f"🗂 Attached to project {filed.get('project_id')}\n"
                                f"({filed.get('project_note','')})"
                            )
                        elif action == "unassigned":
                            reply = (
                                f"📎 Stored in Document Library (no project match)\n"
                                f"hint: {filed.get('hint') or 'none'} — {filed.get('reason','')}"
                            )
                        elif action == "kept_in_library":
                            # Fallback for doc_types that aren't receipts and
                            # aren't in PROJECT_DOC_TYPES — still worth letting
                            # Serge know it landed somewhere.
                            dt = filed.get("doc_type") or "document"
                            reply = (
                                f"📂 Kept in Document Library as {dt}. "
                                f"If this was a receipt, edit the doc type in AHB123 "
                                f"and I'll refile it."
                            )
                        elif filed.get("success") is False:
                            reply = f"⚠️ Filing error: {filed.get('error') or filed}"
                        if reply:
                            await context.bot.send_message(chat_id=chat_id, text=reply)
                    except Exception as e:
                        logger.error(f"[{self.AGENT_ID}] file_document failed: {e}")
                except Exception as e:
                    logger.error(f"[{self.AGENT_ID}] curate failed: {e}")
                    try:
                        await context.bot.send_message(chat_id=chat_id,
                            text=f"(Couldn't auto-analyze the file: {e}\u2014 it's still saved in your Data Hub.)")
                    except Exception:
                        pass

            asyncio.ensure_future(_curate_and_reply())
        except Exception as e:
            logger.error(f"[{self.AGENT_ID}] handle_attachment failed: {e}")
            try:
                await context.bot.send_message(chat_id=chat_id,
                                               text=f"Couldn't save that file: {e}")
            except Exception:
                pass

    async def _create_project_from_photo(self, fpath, caption, chat_id, context, kind="photo"):
        """End-to-end: photo → curator scope → create ahb_projects row → run estimator
        → save as active quote → link photo → rich reply with deep link.

        Any stage can fail and the flow degrades gracefully — the project still gets
        created even if the estimator returns nothing.
        """
        import urllib.request as _ur
        import urllib.error as _ue
        import json as _j
        import datetime as _dt
        import re as _re
        loop = asyncio.get_event_loop()

        def _api(method, path, body=None):
            url = f"http://localhost:8888{path}"
            data = _j.dumps(body).encode() if body is not None else None
            req = _ur.Request(url, data=data, method=method,
                              headers={"Content-Type": "application/json"})
            with _ur.urlopen(req, timeout=60) as r:
                return _j.loads(r.read())

        def _extract_trailing_json(text):
            """Pull the first top-level JSON object from text. Skills print prose
            followed by a JSON dump; we locate the first `{` at column 0 and parse
            with balanced-brace scanning so nested objects don't trip us up."""
            if not text:
                return None
            lines = text.split("\n")
            start_idx = None
            for i, ln in enumerate(lines):
                if ln.startswith("{"):
                    start_idx = i; break
            if start_idx is None:
                return None
            blob = "\n".join(lines[start_idx:])
            # Balanced-brace extract (respect strings)
            depth = 0; in_str = False; esc = False
            for i, ch in enumerate(blob):
                if esc: esc = False; continue
                if ch == "\\" and in_str: esc = True; continue
                if ch == '"': in_str = not in_str; continue
                if in_str: continue
                if ch == "{": depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try: return _j.loads(blob[:i+1])
                        except Exception: return None
            return None

        async def _send(text, parse_mode="HTML"):
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
            except Exception:
                # Retry without HTML parse mode if Telegram rejects formatting
                try:
                    plain = _re.sub(r"<[^>]+>", "", text)
                    await context.bot.send_message(chat_id=chat_id, text=plain)
                except Exception as e:
                    logger.error(f"[{self.AGENT_ID}] send failed in create_project: {e}")

        try:
            # ── Stage 1: vision via curator ────────────────────────────────
            await _send("📸 <i>Analyzing photo for scope of work…</i>")
            def _run_curator():
                return self.skills.run("curate_document",
                    {"file_path": fpath, "agent_id": self.AGENT_ID, "chat_id": chat_id})
            curator_result = await loop.run_in_executor(None, _run_curator)
            analysis = {}
            try:
                if isinstance(curator_result, dict):
                    if "output" in curator_result:
                        analysis = _extract_trailing_json(curator_result["output"]) or {}
                    else:
                        analysis = curator_result
                elif isinstance(curator_result, str):
                    analysis = _extract_trailing_json(curator_result) or {}
            except Exception:
                analysis = {}
            if not isinstance(analysis, dict):
                analysis = {}

            summary = (analysis.get("summary") or "").strip()
            relevance = (analysis.get("relevance") or "").strip()
            entity_hint = (analysis.get("entity") or "").strip()
            project_hint = (analysis.get("project_hint") or "").strip()
            doc_type = (analysis.get("doc_type") or "").strip()
            description_blob = "\n\n".join(filter(None, [summary, relevance])) \
                or caption or f"Project photo {os.path.basename(fpath)}"

            # ── Stage 2: infer scope + sqft ────────────────────────────────
            combined = f"{caption} {summary} {relevance} {project_hint}".lower()
            scope_map = [
                ("kitchen",  ["kitchen", "cabinets", "countertop", "range", "stove"]),
                ("bathroom", ["bathroom", "bath", "shower", "toilet", "vanity", "tub"]),
                ("basement", ["basement", "cellar", "egress"]),
                ("addition", ["addition", "extension", "add-on", "expansion"]),
                ("deck",     ["deck", "patio", "porch"]),
                ("full-reno",["gut renovation", "full reno", "whole house", "complete renovation"]),
            ]
            scope = "other"
            for s, kws in scope_map:
                if any(kw in combined for kw in kws):
                    scope = s; break

            sqft = 0
            m = _re.search(r"(\d{2,5})\s*(?:sq\s*ft|sqft|square\s*feet)", combined)
            if m:
                try: sqft = int(m.group(1))
                except: sqft = 0
            if not sqft:
                m = _re.search(r"(\d{1,3})\s*(?:x|×|by)\s*(\d{1,3})", combined)
                if m:
                    try: sqft = int(m.group(1)) * int(m.group(2))
                    except: sqft = 0

            # ── Stage 3: derive title + address ────────────────────────────
            address_match = _re.search(r"\b(\d{2,5}\s+[A-Za-z][\w\s]{2,40}(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place))\b", caption + " " + summary, _re.IGNORECASE)
            address = address_match.group(1).strip() if address_match else ""
            today_str = _dt.date.today().strftime("%b %d %Y")
            if project_hint:
                title = f"{scope.title() if scope != 'other' else 'Project'} — {project_hint[:60]}"
            elif address:
                title = f"{scope.title() if scope != 'other' else 'Project'} — {address}"
            elif caption and len(caption) < 80:
                title = caption
            else:
                title = f"New {scope.title() if scope != 'other' else 'Project'} — {today_str}"
            title = title[:120]

            # ── Stage 4: create project ────────────────────────────────────
            proj_payload = {
                "title": title,
                "description": description_blob[:2000],
                "scope": scope,
                "status": "Planning",
                "start_date": _dt.date.today().isoformat(),
                "address": address,
                "location": address,
                "client_name": entity_hint if entity_hint and entity_hint.lower() not in ("unknown", "unknown entity") else "",
                "assigned_agents": self.AGENT_ID,
                "notes": f"Created from photo by {self.AGENT_ID} on {_dt.datetime.now().isoformat()}\n"
                         f"Source: {os.path.basename(fpath)}\n"
                         f"Caption: {caption}" if caption else
                         f"Created from photo by {self.AGENT_ID} on {_dt.datetime.now().isoformat()}\n"
                         f"Source: {os.path.basename(fpath)}",
            }
            try:
                proj_resp = await loop.run_in_executor(None,
                    lambda: _api("POST", "/api/ahb/projects", proj_payload))
            except Exception as e:
                await _send(f"❌ Couldn't create project: {e}")
                return
            if not proj_resp.get("success"):
                await _send(f"❌ Couldn't create project: {proj_resp.get('error','unknown')}")
                return
            project_id = proj_resp.get("id")
            project_url = f"http://localhost:8888/ahb123?project={project_id}"

            await _send(
                f"📋 <b>Project created:</b> {title}\n"
                f"Status: Planning · Scope: {scope}"
                + (f" · ~{sqft} sqft" if sqft else "")
                + (f"\nAddress: {address}" if address else "")
                + f"\nID: <code>{project_id}</code>\n\n"
                f"💰 <i>Running AHBCO estimator…</i>"
            )

            # ── Stage 5: estimator ─────────────────────────────────────────
            def _run_est():
                args = {"description": description_blob + (f"\n\nCaption: {caption}" if caption else ""),
                        "scope": scope}
                if sqft: args["sqft"] = sqft
                return self.skills.run("estimate_project", args)
            est_result = await loop.run_in_executor(None, _run_est)
            estimate = None
            est_text = ""
            if isinstance(est_result, dict):
                if "grand_total_low" in est_result:
                    estimate = est_result
                else:
                    est_text = est_result.get("output", "") or ""
            elif isinstance(est_result, str):
                est_text = est_result
            if not estimate and est_text:
                parsed = _extract_trailing_json(est_text)
                if isinstance(parsed, dict) and "grand_total_low" in parsed:
                    estimate = parsed

            # ── Stage 6: save quote + link photo ───────────────────────────
            quote_saved = False
            if estimate and estimate.get("grand_total_mid"):
                quote_payload = {
                    "method": "photo_estimator",
                    "scope": scope,
                    "description": f"Auto-generated by {self.AGENT_ID} from photo. {description_blob[:400]}",
                    "total": estimate["grand_total_mid"],
                    "breakdown": {
                        "line_items": estimate.get("line_items", []),
                        "subtotal_low": estimate.get("subtotal_low"),
                        "subtotal_high": estimate.get("subtotal_high"),
                        "grand_total_low": estimate.get("grand_total_low"),
                        "grand_total_high": estimate.get("grand_total_high"),
                        "grand_total_mid": estimate.get("grand_total_mid"),
                        "markup_pct": estimate.get("markup_pct"),
                        "sqft": estimate.get("sqft"),
                        "location": estimate.get("location"),
                    },
                    "notes": f"Source photo: {os.path.basename(fpath)} · Agent: {self.AGENT_ID}",
                    "make_active": True,
                }
                try:
                    q_resp = await loop.run_in_executor(None,
                        lambda: _api("POST", f"/api/ahb/projects/{project_id}/quotes", quote_payload))
                    quote_saved = bool(q_resp.get("success"))
                except Exception as e:
                    logger.warning(f"[{self.AGENT_ID}] save quote failed: {e}")

            # Link the source photo as a project file
            try:
                await loop.run_in_executor(None, lambda: _api("POST", "/api/ahb/files", {
                    "name": os.path.basename(fpath),
                    "file_type": "image",
                    "file_path": fpath,
                    "project_id": project_id,
                    "category": "project_photo",
                    "tags": f"{self.AGENT_ID},auto,scope_{scope}",
                }))
            except Exception as e:
                logger.warning(f"[{self.AGENT_ID}] link photo failed: {e}")

            # ── Stage 7: rich final reply ─────────────────────────────────
            lines = [
                "🏗️ <b>PROJECT CREATED + ESTIMATE READY</b>",
                "━━━━━━━━━━━━━━━━━━━",
                f"📋 <b>{title}</b>",
                f"Status: Planning · Scope: <b>{scope}</b>" + (f" · ~{sqft} sqft" if sqft else ""),
            ]
            if address:
                lines.append(f"📍 {address}")
            if entity_hint and entity_hint.lower() not in ("unknown", "unknown entity"):
                lines.append(f"👤 Client hint: {entity_hint}")
            lines.append(f"🆔 <code>{project_id}</code>")
            lines.append("")
            lines.append("📸 <b>SCOPE ANALYSIS</b>")
            if summary:
                lines.append(summary[:600])
            if relevance and relevance != summary:
                lines.append(f"<i>{relevance[:300]}</i>")
            lines.append("")

            if estimate and estimate.get("grand_total_mid"):
                low = estimate["grand_total_low"]; high = estimate["grand_total_high"]; mid = estimate["grand_total_mid"]
                lines.append("💰 <b>ESTIMATED COST</b>")
                lines.append(f"Range: <b>${low:,} – ${high:,}</b>")
                lines.append(f"Mid-point: ${mid:,} · Saved as active quote" if quote_saved else
                              f"Mid-point: ${mid:,}")
                lines.append("")
                top_items = sorted(estimate.get("line_items", []),
                                    key=lambda x: x.get("total_mid", 0), reverse=True)[:8]
                if top_items:
                    lines.append("<b>Top line items:</b>")
                    for it in top_items:
                        lines.append(f"  • {it.get('category','?')}: ${it.get('total_low',0):,} – ${it.get('total_high',0):,}")
                    lines.append("")
                markup = estimate.get("markup_pct", 15)
                lines.append(f"<i>Includes 10% contingency + {markup}% GC markup. Philadelphia-area 2026 rates.</i>")
            else:
                lines.append("⚠️ <i>Estimator couldn't produce numbers from this photo alone — "
                             "reply with scope details (sqft, finishes, demo yes/no) and I'll re-run it.</i>")
            lines.append("")
            lines.append(f"📂 <a href=\"{project_url}\">Open project in dashboard</a>")
            lines.append("")
            lines.append("<i>Reply with more details to refine the estimate (client name, address, "
                         "finishes, sqft, demo scope, timeline).</i>")

            await _send("\n".join(lines))

            # Journal + memory
            self.journal("project_created",
                         f"Created project {project_id} ({scope}, {title[:60]}) from photo",
                         chat_id=chat_id)
            if hasattr(self, "set_memory"):
                try:
                    self.set_memory("last_project_created",
                                    f"{project_id}|{title}", category="projects")
                except Exception:
                    pass
            return project_id
        except Exception as e:
            logger.error(f"[{self.AGENT_ID}] _create_project_from_photo crashed: {e}")
            try:
                await context.bot.send_message(chat_id=chat_id,
                    text=f"❌ Project creation crashed: {e}\n\nThe photo is still saved in your Data Hub.")
            except Exception:
                pass
            return None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text or ""

        if not text.strip():
            return

        logger.info(f"[{self.AGENT_ID}] Message from {chat_id}: {text[:80]}")

        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Track message count for this chat session
        self._message_counts[chat_id] = self._message_counts.get(chat_id, 0) + 1

        # ── DocPrep intent intercept (fires for ALL agents) ───────────────────
        doc_intent = self._detect_doc_intent(text)
        if doc_intent:
            doc_reply = await self._handle_doc_intent(doc_intent, text, chat_id)
            if doc_reply:
                save_message(chat_id, self.AGENT_ID, "user", text)
                save_message(chat_id, self.AGENT_ID, "assistant", doc_reply)
                self.journal("doc_intent_handled",
                             f"{doc_intent['action']}: {text[:100]}",
                             chat_id=chat_id)
                await self._send_response(context.bot, chat_id, doc_reply)
                return

        # ── Task creation intercept (fires for ALL agents) ────────────────────
        task_confirm = self._try_create_task_from_message(text)
        if task_confirm:
            save_message(chat_id, self.AGENT_ID, "user", text)
            save_message(chat_id, self.AGENT_ID, "assistant", task_confirm)
            await self._send_response(context.bot, chat_id, task_confirm)
            return

        # ── Print request intercept (fires for ALL agents) ────────────────
        if self._is_print_request(text):
            save_message(chat_id, self.AGENT_ID, "user", text)
            await context.bot.send_message(chat_id=chat_id, text="Sending to printer...")
            reply = await self._handle_print_request(text, chat_id)
            save_message(chat_id, self.AGENT_ID, "assistant", reply)
            await self._send_response(context.bot, chat_id, reply)
            return

        # ── Directive intent intercept (fires for ALL agents) ─────────────
        if text.lstrip().startswith("/"):
            directive_reply = self._maybe_handle_directive(text)
            if directive_reply is not None:
                save_message(chat_id, self.AGENT_ID, "user", text)
                save_message(chat_id, self.AGENT_ID, "assistant", directive_reply)
                self.journal("directive_handled", text[:200], chat_id=chat_id)
                await self._send_response(context.bot, chat_id, directive_reply)
                return

        # Save incoming message to DB
        save_message(chat_id, self.AGENT_ID, "user", text)
        self.journal("message_received", f"User: {text[:200]}", chat_id=chat_id)

        # Build conversation history for LLM — trim long messages to keep context tight
        history = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = []
        for h in history:
            content = h["content"]
            if len(content) > 500:
                content = content[:497] + "..."
            messages.append({"role": h["role"], "content": content})

        # ── Graft 5: route to a task-specialized model if MODEL_ROUTING is set ──
        routed_model = self._route_model(text)
        if routed_model != self.MODEL:
            logger.info(f"[{self.AGENT_ID}] routing to {routed_model} (text: {text[:60]!r})")

        loop = asyncio.get_event_loop()
        t0 = time.time()

        if scaffold_config.is_enabled(self.AGENT_ID):
            # ── Scaffold path: select relevant skills, then run the bounded
            # plan→act→observe→finish loop. Static skill blocks from
            # build_system_prompt remain as the pinned fallback baseline. ──
            from core import skill_selector, agent_loop
            sel = skill_selector.select(text, agent_id=self.AGENT_ID,
                                        pinned=scaffold_config.pinned_core(),
                                        role_pins=[],
                                        top_k=scaffold_config.retrieval_top_k())
            system = self.build_system_prompt(extra=skill_selector.render_block(sel))

            def _llm(msgs, sysp):
                return self.llm_chat(msgs, sysp, model_override=routed_model)

            res = await loop.run_in_executor(
                None, lambda: agent_loop.run_loop(
                    _llm, self.skills, system=system, user=text,
                    max_steps=scaffold_config.max_steps(),
                    parse_kwargs={"chat_id": chat_id},
                    history=messages))  # prior turns preserve multi-turn context
            response = res["final"] or "_(no response)_"
            # Surface skill results so the failed-skill warning and the
            # _auto_save_artifact dedupe guard work the same as the legacy path.
            skill_results = res.get("results", [])
        else:
            # ── Legacy path (unchanged): single shot + two-pass reground ──
            system = self.build_system_prompt()

            # ── Pass 1: LLM decides what to do (may emit ##SKILL:## calls) ────────
            response = await loop.run_in_executor(
                None, lambda: self.llm_chat(messages, system, model_override=routed_model)
            )

            if not response:
                response = "_(no response)_"

            # ── Skills: parse and execute any ##SKILL:## calls ─────────────────
            response, skill_results = self.skills.parse_and_run(response, chat_id=chat_id)

            # ── Pass 2: if skills ran successfully, reformat with real data ────
            successful_skills = [r for r in skill_results if r.get("success")]
            if successful_skills:
                skill_data = "\n\n".join(
                    f"[{r['skill']} output]\n{r['output']}" for r in successful_skills
                )
                reformat_messages = [
                    {
                        "role": "user",
                        "content": (
                            f"Original request from Serge: {text}\n\n"
                            f"Here is the REAL live data from your skills:\n\n{skill_data}\n\n"
                            f"Now format this into your standard response style. "
                            f"Use the real data above — do NOT invent or estimate any values."
                        )
                    }
                ]
                response = await loop.run_in_executor(
                    None, self.llm_chat, reformat_messages, system
                )
                if not response:
                    # Fallback: just return the raw skill output if reformat fails
                    response = skill_data

        duration_ms = int((time.time() - t0) * 1000)

        # Report any failed skills
        failed_skills = [r for r in skill_results if not r.get("success")]
        if failed_skills:
            response += f"\n\n⚠️ Skill errors: " + \
                       ", ".join(f"{r.get('skill','?')}: {r.get('error','unknown')}" for r in failed_skills)

        # Auto-save artifact if response contains substantial generated content
        self._auto_save_artifact(chat_id, text, response, skill_results)

        # Save response to DB
        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self.journal(
            task_type="llm_response",
            description=f"Responded to: {text[:100]}",
            result=response[:300],
            success=True,
            chat_id=chat_id
        )

        # Auto-remember key context from this exchange
        self._auto_remember(chat_id, text, response)

        # Auto-summarize if session is getting long
        if self._message_counts[chat_id] % AUTO_SUMMARIZE_AFTER == 0:
            await self._auto_summarize(chat_id, history, context.bot)

        # Send response (split if too long for Telegram)
        await self._send_response(context.bot, chat_id, response)

    # ── Auto Artifact Save ────────────────────────────────────────────────────

    _ARTIFACT_EXT_MAP = {
        "python": ".py", "py": ".py",
        "javascript": ".js", "js": ".js", "typescript": ".ts", "ts": ".ts",
        "html": ".html", "css": ".css",
        "json": ".json", "yaml": ".yml", "yml": ".yml",
        "bash": ".sh", "sh": ".sh", "shell": ".sh",
        "sql": ".sql", "svg": ".svg",
    }

    def _auto_save_artifact(self, chat_id: int, user_msg: str, response: str, skill_results: list):
        """
        Automatically save the agent's response as an artifact if it contains
        substantial generated content. Skips if an explicit artifact_save skill
        already ran. Called after every handle_message cycle.
        """
        # Skip if the agent already saved via an explicit skill call
        if any(r.get("skill") == "artifact_save" and r.get("success") for r in skill_results):
            return

        # Detect fenced code blocks: ```lang\n...\n```
        code_blocks = re.findall(r'```(\w*)\n([\s\S]+?)```', response)
        has_any_code = len(code_blocks) > 0
        has_substantial_code = any(
            len(code.strip().splitlines()) >= 2
            for _, code in code_blocks
        )

        # Detect structured documents (headers + length)
        has_structured_doc = (
            len(response) > 400
            and bool(re.search(r'^#+\s', response, re.MULTILINE))
        )

        # Detect bullet list output (4+ bullets)
        has_list_output = (
            len(response) > 300
            and len(re.findall(r'^[-*•]\s', response, re.MULTILINE)) >= 4
        )

        # Detect markdown tables
        has_table = '|---|' in response or '| --- |' in response or '|:---|' in response

        if not (has_substantial_code or has_any_code or has_structured_doc or has_list_output or has_table):
            return

        # Determine extension from first code block language (fallback .md)
        ext = ".md"
        if code_blocks:
            lang = code_blocks[0][0].lower()
            ext = self._ARTIFACT_EXT_MAP.get(lang, ".md")

        # Auto-detect project from content
        from skills.shared.save_artifact import detect_project
        project_id = detect_project(response + " " + user_msg)

        ts = int(time.time())
        safe_user = re.sub(r'[^\w]', '_', user_msg[:30]).strip('_')
        filename = f"{self.AGENT_ID}_{ts}_{safe_user}{ext}"

        try:
            result = self.save_artifact(filename, response, project_id=project_id)
            if result.get("success"):
                logger.info(f"[{self.AGENT_ID}] Auto-saved artifact: {filename} → {project_id}")
            else:
                logger.debug(f"[{self.AGENT_ID}] Auto-save skipped: {result.get('error','')}")
        except Exception as e:
            logger.debug(f"[{self.AGENT_ID}] Auto-save failed: {e}")

    # ── Auto Memory ───────────────────────────────────────────────────────────

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        """
        Look for memory-worthy patterns in the exchange and persist them.
        Agents can override this for domain-specific extraction.
        """
        task_patterns = [
            r'(?:working on|building|fixing|deploying|setting up)\s+(.+?)(?:\.|$)',
            r'(?:task is|my job is|need to)\s+(.+?)(?:\.|$)',
        ]
        for pattern in task_patterns:
            m = re.search(pattern, user_msg, re.IGNORECASE)
            if m:
                self.remember(f"last_task_chat_{chat_id}", m.group(1)[:200], "tasks")
                break

        self.remember("last_active_chat_id", str(chat_id), "session")

    # ── Event Bus Listener ─────────────────────────────────────────────────────

    async def start_event_listener(self):
        """Listen for events relevant to this agent."""
        if not self.event_bus:
            return
        try:
            async for event in self.event_bus.listen("research_complete", "agent_alert", "knowledge_updated"):
                if event.type == "agent_alert" and event.data.get("target") != self.agent_id:
                    continue  # Not for us
                await self._handle_event(event)
        except Exception as e:
            logger.error(f"[{self.AGENT_ID}] Event listener error: {e}")

    async def _handle_event(self, event):
        """Override in subclasses for custom event handling."""
        logger.info(f"[{self.agent_id}] Received event: {event}")

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Emit a Redis heartbeat every 60 seconds so the dashboard shows liveness."""
        try:
            import redis as _redis
            _redis_host = os.environ.get("BAZA_REDIS_HOST", "localhost")
            _redis_port = int(os.environ.get("BAZA_REDIS_PORT", "6379"))
            r = _redis.Redis(host=_redis_host, port=_redis_port, decode_responses=True)
        except Exception:
            return
        while True:
            try:
                import json as _json
                payload = _json.dumps({
                    "agent_id": self.AGENT_ID,
                    "model": self.MODEL,
                    "status": "idle",
                    "ts": int(time.time()),
                })
                r.setex(f"baza:heartbeat:{self.AGENT_ID}", 120, payload)
            except Exception:
                pass
            await asyncio.sleep(60)

    # ── Artifact Context Loop ──────────────────────────────────────────────────

    async def _artifact_context_loop(self):
        """Refresh shared artifact knowledge in empire_knowledge every 5 minutes."""
        import os as _os
        artifacts_dir = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "dashboard", "artifacts"
        )
        while True:
            try:
                from core.context_db import update_artifact_empire_knowledge
                update_artifact_empire_knowledge(artifacts_dir)
            except Exception:
                pass
            await asyncio.sleep(300)

    # ── Auto Summarize ────────────────────────────────────────────────────────

    async def _auto_summarize(self, chat_id: int, history: list, bot: Bot):
        """
        Ask the LLM to compress recent conversation into a summary,
        then save it to agent_summaries table.
        """
        logger.info(f"[{self.AGENT_ID}] Auto-summarizing chat {chat_id}...")
        recent = history[-AUTO_SUMMARIZE_AFTER:]
        history_text = "\n".join(
            f"{h['role'].upper()}: {h['content'][:200]}" for h in recent
        )
        summarize_prompt = (
            "You are summarizing a conversation for long-term memory. "
            "Write a concise 2-3 sentence summary of what was discussed and decided. "
            "Focus on facts, decisions, and outcomes. Be specific."
        )
        summary_messages = [{"role": "user", "content": f"Summarize this conversation:\n\n{history_text}"}]

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            None, self.llm_chat, summary_messages, summarize_prompt
        )
        if summary:
            self.summarize(summary, chat_id=chat_id, message_count=len(recent))
            logger.info(f"[{self.AGENT_ID}] Summary saved: {summary[:100]}")

    # ── Response Sender ───────────────────────────────────────────────────────


    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Plain-text fallback — delegated to core.telegram_fmt."""
        from core.telegram_fmt import strip_markdown
        return strip_markdown(text)

    async def _send_response(self, bot: Bot, chat_id: int, text: str):
        """Send response, splitting into chunks if > 4096 chars (Telegram limit).

        Anti-hallucination post-check: tag completion claims with no matching
        artifact in the last 2h. Disable per-message by setting
        BAZA_VERIFY_TELEGRAM=0 env. Failures of the verifier never block sends.
        """
        # Guard: never send raw dicts/objects to Telegram
        if not isinstance(text, str):
            text = str(text)
        if os.environ.get("BAZA_VERIFY_TELEGRAM", "1") not in ("0", "false", "no"):
            try:
                from core.claim_verifier import annotate_unverified
                text, report = annotate_unverified(text, hours=2, agent=self.AGENT_ID)
                if not report["verified"]:
                    logger.info(
                        f"[{self.AGENT_ID}] post-check: {report['unbacked_count']} "
                        f"unbacked claim(s) tagged (artifacts in window: "
                        f"{report['artifact_count']})"
                    )
                    # Auto-DISPATCH self-correction: when an agent claims work
                    # done with no artifact, append a DISPATCH line that the
                    # task_runner picks up as a forwarded directive. The agent
                    # is dispatched to itself with a clear "re-do and actually
                    # save the deliverable" instruction. Disable per-process
                    # via BAZA_AUTO_DISPATCH_ON_HALLUCINATION=0.
                    auto = os.environ.get("BAZA_AUTO_DISPATCH_ON_HALLUCINATION", "1")
                    if auto not in ("0", "false", "no") and report["unbacked_count"] > 0:
                        unbacked = [c for c in report["claims"] if not c["backed"]]
                        first = unbacked[0]["sentence"][:160] if unbacked else ""
                        text += (
                            f"\n\nDISPATCH:{self.AGENT_ID}:Re-do and actually "
                            f"save the deliverable for: {first}. Use "
                            f"##SKILL:artifact_save## with a concrete file. "
                            f"Reply with the artifact path."
                        )
                        try:
                            from core import task_events as _te
                            _te.emit("dispatch_sent",
                                     agent_id=self.AGENT_ID,
                                     payload={
                                         "to_agent": self.AGENT_ID,
                                         "instruction_snippet": "auto-dispatch: hallucination self-correction",
                                         "trigger": "claim_verifier",
                                         "unbacked_count": report["unbacked_count"],
                                     })
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[{self.AGENT_ID}] claim_verifier skipped: {e}")
        from core.telegram_fmt import send_html
        await send_html(bot, chat_id, text)

    # ── Cron alert buttons (Ack / Snooze / Task) ────────────────────────────────

    async def _on_cron_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle a tap on a cron alert's inline Ack / Snooze / Task button.

        callback_data is "cron|<action>|<row_id>" (built by
        agents/cron_helpers.py send_alert). This handler must never let an
        exception escape -- a bad/garbled callback or a DB hiccup degrades to
        query.answer("failed: ...") instead of crashing the bot's update
        loop. query.answer() is always called exactly once so Telegram
        clears the button's loading spinner either way.

        Hardening:
          - alert_ack()/alert_snooze() are blind UPDATEs -- they "succeed"
            (0 rows affected, no exception) even against a row_id that no
            longer exists in cron_alert_state. So existence is checked
            explicitly via cron_health_db.alert_get() before any mutation;
            a missing row answers query.answer("failed: unknown alert")
            instead of a fake success.
          - A repeated tap on a message whose text already ends with this
            action's suffix (i.e. it was already actioned) is treated as an
            idempotent no-op: no second DB mutation, no second task, no
            re-edit -- it just re-answers the same success text.
        """
        query = getattr(update, "callback_query", None)
        if query is None:
            return
        try:
            data = query.data or ""
            parts = data.split("|")
            if len(parts) != 3 or parts[0] != "cron" or parts[1] not in CRON_CALLBACK_ACTIONS:
                await query.answer(f"failed: bad callback data {data!r}")
                return

            _, action, row_id_s = parts
            try:
                row_id = int(row_id_s)
            except ValueError:
                await query.answer(f"failed: bad row id {row_id_s!r}")
                return

            old_text = getattr(query.message, "text", "") or ""
            suffix = "\n\n" + CRON_CALLBACK_ACTIONS[action]

            try:
                from core import cron_health_db
                row = cron_health_db.alert_get(row_id)
                if row is None:
                    await query.answer("failed: unknown alert")
                    return

                if old_text.endswith(suffix):
                    # Already actioned by a previous tap -- idempotent
                    # no-op: don't re-mutate cron_alert_state, don't create
                    # a second task, don't re-edit the message.
                    await query.answer(CRON_CALLBACK_ACTIONS[action])
                    return

                if action == "ack":
                    cron_health_db.alert_ack(row_id)
                elif action == "snooze":
                    cron_health_db.alert_snooze(row_id, 24)
                elif action == "task":
                    title = "cron alert"
                    if row["meta"]:
                        try:
                            meta = json.loads(row["meta"])
                            title = meta.get("title") or title
                        except Exception:
                            pass
                    self.tasks.add("shared", title, priority="medium")
            except Exception as e:
                logger.error(f"[{self.AGENT_ID}] cron callback {action} (row {row_id}) failed: {e}")
                await query.answer(f"failed: {e}")
                return

            try:
                await query.edit_message_text(old_text + suffix)
            except Exception as e:
                logger.warning(f"[{self.AGENT_ID}] cron callback edit_message_text failed: {e}")

            await query.answer(CRON_CALLBACK_ACTIONS[action])
        except Exception as e:
            logger.error(f"[{self.AGENT_ID}] cron callback handler crashed: {e}")
            try:
                await query.answer(f"failed: {e}")
            except Exception:
                pass

    # ── Bot Runner ────────────────────────────────────────────────────────────

    async def run(self):
        token = os.environ.get(self.TOKEN_ENV)
        if not token:
            raise ValueError(f"[{self.AGENT_ID}] Missing token: {self.TOKEN_ENV}")

        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        # Capture every photo / document / video / audio sent in chat → save to datahub
        app.add_handler(MessageHandler(
            filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE,
            self.handle_attachment
        ))
        # Inline Ack / Snooze / Task buttons on cron alerts (agents/cron_helpers.py
        # send_alert). callback_data is "cron|<action>|<row_id>".
        app.add_handler(CallbackQueryHandler(self._on_cron_callback, pattern=r"^cron\|"))

        logger.info(f"[{self.AGENT_ID}] Starting Telegram bot...")

        # PTB v20+ async with pattern — safe inside existing event loop
        async with app:
            await app.initialize()
            await app.start()
            # Start background loops: heartbeat + artifact context refresh + event bus
            asyncio.ensure_future(self._heartbeat_loop())
            asyncio.ensure_future(self._artifact_context_loop())
            asyncio.ensure_future(self.start_event_listener())
            await app.updater.start_polling(drop_pending_updates=True)
            # Keep running until cancelled
            try:
                await asyncio.Event().wait()
            finally:
                await app.updater.stop()
                await app.stop()
