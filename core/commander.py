"""
Baza Empire — Simon Commander Module
--------------------------------------
Simon receives orders from Serge, builds a task brief, dispatches
instructions to agents via Telegram AND fires the right tool endpoints
automatically. Collects results and reports a final summary to Serge.
"""

import os
import json
import time
import logging
import asyncio
import httpx
import redis as redis_lib

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TOOL_SERVER = "http://localhost:8000"

# ─── Agent registry ───────────────────────────────────────────────────────────
AGENT_REGISTRY = {
    'claw_batto': {'token_env': 'TELEGRAM_CLAW_BATTO', 'name': 'Claw Batto', 'slug': 'claw'},
    'phil_hass':  {'token_env': 'TELEGRAM_PHIL_HASS',  'name': 'Phil Hass',  'slug': 'phil'},
    'sam_axe':    {'token_env': 'TELEGRAM_SAM_AXE',    'name': 'Sam Axe',    'slug': 'sam'},
}

# ─── Tool routing map ─────────────────────────────────────────────────────────
# Keywords in Simon's instruction → which tool to auto-fire and with what input
TOOL_ROUTES = {
    'claw_batto': [
        {
            'keywords': ['mining status', 'mining', 'miner'],
            'tool': 'mining-status',
            'input_builder': lambda inst: {}
        },
        {
            'keywords': ['docker', 'containers', 'container'],
            'tool': 'docker-status',
            'input_builder': lambda inst: {}
        },
        {
            'keywords': ['disk', 'storage', 'space'],
            'tool': 'disk-usage',
            'input_builder': lambda inst: {}
        },
        {
            'keywords': ['service', 'status', 'running', 'check'],
            'tool': 'service-status',
            'input_builder': lambda inst: {'service': _extract_service(inst)}
        },
        {
            'keywords': ['restart'],
            'tool': 'restart-service',
            'input_builder': lambda inst: {'service': _extract_service(inst)}
        },
        {
            'keywords': ['run', 'execute', 'command', 'shell'],
            'tool': 'run-command',
            'input_builder': lambda inst: {'command': _extract_command(inst)}
        },
    ],
    'phil_hass': [
        {
            'keywords': ['invoice', 'bill', 'billing'],
            'tool': 'generate-invoice',
            'input_builder': lambda inst: _parse_invoice(inst)
        },
        {
            'keywords': ['tax', 'taxes', 'irs', 'quarterly'],
            'tool': 'tax-summary',
            'input_builder': lambda inst: {}
        },
        {
            'keywords': ['contract', 'agreement', 'contractor'],
            'tool': 'contract-template',
            'input_builder': lambda inst: _parse_contract(inst)
        },
    ],
    'sam_axe': [
        {
            'keywords': ['crypto', 'price', 'prices', 'xmr', 'rvn', 'bitcoin', 'coin'],
            'tool': 'crypto-prices',
            'input_builder': lambda inst: {'coins': ['monero', 'ravencoin', 'bitcoin']}
        },
        {
            'keywords': ['research', 'search', 'find', 'look up', 'market'],
            'tool': 'market-research',
            'input_builder': lambda inst: {'query': inst[:200]}
        },
        {
            'keywords': ['scrape', 'fetch', 'website', 'url', 'http'],
            'tool': 'scrape-web',
            'input_builder': lambda inst: {'url': _extract_url(inst)}
        },
        {
            'keywords': ['kpi', 'report', 'metrics', 'dashboard', 'analytics'],
            'tool': 'kpi-report',
            'input_builder': lambda inst: {'title': 'Empire KPI Report', 'metrics': {}}
        },
    ]
}


# ─── Input extraction helpers ─────────────────────────────────────────────────

def _extract_service(inst: str) -> str:
    known = ['baza-mining', 'baza-nuc-mining', 'baza-agent-simon-bately',
             'baza-agent-claw-batto', 'baza-agent-phil-hass', 'baza-agent-sam-axe',
             'baza-tool-server', 'docker', 'redis', 'postgresql', 'nginx', 'mosquitto']
    for s in known:
        if s in inst.lower():
            return s
    return 'baza-tool-server'

def _extract_command(inst: str) -> str:
    # Pull out anything after "run:" or "execute:" or use a safe default
    for prefix in ['run:', 'execute:', 'command:']:
        if prefix in inst.lower():
            idx = inst.lower().index(prefix) + len(prefix)
            return inst[idx:].strip().split('\n')[0]
    return 'uptime'

def _extract_url(inst: str) -> str:
    import re
    match = re.search(r'https?://[^\s]+', inst)
    return match.group(0) if match else ''

def _parse_invoice(inst: str) -> dict:
    return {
        'client_name': 'Client',
        'items': [{'description': 'Services rendered', 'amount': 0.00}],
        'invoice_number': f'INV-{int(time.time())}'
    }

def _parse_contract(inst: str) -> dict:
    return {
        'contractor_name': '[CONTRACTOR]',
        'scope': inst[:300],
        'rate': '[TO BE DETERMINED]',
        'start_date': time.strftime('%Y-%m-%d')
    }


# ─── Route tool for instruction ───────────────────────────────────────────────

def route_tool(agent_id: str, instruction: str):
    """Find the best matching tool for this agent + instruction."""
    routes = TOOL_ROUTES.get(agent_id, [])
    inst_lower = instruction.lower()
    for route in routes:
        if any(kw in inst_lower for kw in route['keywords']):
            return route['tool'], route['input_builder'](instruction)
    return None, None


# ─── Commander ────────────────────────────────────────────────────────────────

class SimonCommander:
    def __init__(self, redis_client: redis_lib.Redis, serge_chat_id: str, simon_token: str):
        self.redis = redis_client
        self.serge_chat_id = serge_chat_id
        self.simon_token = simon_token

    # ─── Dispatch: Telegram message + optional tool call ─────────────────────

    async def dispatch_async(self, agent_id: str, instruction: str, task_id: str) -> bool:
        agent = AGENT_REGISTRY.get(agent_id)
        if not agent:
            return False

        token = os.environ.get(agent['token_env'])
        chat_id = self.redis.get(f"agent:{agent_id}:serge_chat_id")

        results = {}

        # ── Fire tool endpoint if we can route it ────────────────────────────
        tool_name, tool_input = route_tool(agent_id, instruction)
        if tool_name:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{TOOL_SERVER}/tools/{agent['slug']}/{tool_name}",
                        json={"input": tool_input, "task_id": task_id}
                    )
                    tool_result = resp.json()
                    results['tool'] = tool_result
                    logger.info(f"Tool {agent['slug']}/{tool_name} fired for {task_id}: "
                                f"success={tool_result.get('success')}")
            except Exception as e:
                logger.error(f"Tool call failed for {task_id}: {e}")
                results['tool'] = {'success': False, 'error': str(e)}

        # ── Send Telegram dispatch to agent ──────────────────────────────────
        if token and chat_id:
            tool_note = f"\n\n[Tool fired: {tool_name}]" if tool_name else ""
            message = (
                f"[TASK:{task_id}] Simon says:\n\n"
                f"{instruction}{tool_note}\n\n"
                f"Report back with: REPORT:{task_id}:<your full report>"
            )
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        TELEGRAM_API.format(token=token, method="sendMessage"),
                        json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
                    )
                    results['telegram'] = resp.ok
            except Exception as e:
                logger.error(f"Telegram dispatch failed for {task_id}: {e}")
                results['telegram'] = False

        # ── Store task state ──────────────────────────────────────────────────
        self.redis.hset(f"task:{task_id}", mapping={
            "agent_id": agent_id,
            "agent_name": agent['name'],
            "instruction": instruction[:500],
            "status": "dispatched",
            "tool_fired": tool_name or "none",
            "tool_result": json.dumps(results.get('tool', {})),
            "dispatched_at": str(time.time())
        })
        self.redis.expire(f"task:{task_id}", 3600)

        # ── If tool already returned a result, mark complete immediately ──────
        tool_res = results.get('tool', {})
        if tool_res.get('success'):
            self.redis.hset(f"task:{task_id}", mapping={
                "status": "complete",
                "report": json.dumps(tool_res.get('output', {})),
                "completed_at": str(time.time())
            })
            logger.info(f"Task {task_id} auto-completed via tool result")

        return True

    def dispatch_to_agent(self, agent_id: str, instruction: str, task_id: str) -> bool:
        """Sync wrapper — runs async dispatch in event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.dispatch_async(agent_id, instruction, task_id))
                return True
            else:
                return loop.run_until_complete(
                    self.dispatch_async(agent_id, instruction, task_id)
                )
        except Exception as e:
            logger.error(f"Dispatch error: {e}")
            return False

    # ─── Job orchestration ───────────────────────────────────────────────────

    def create_job(self, job_id: str, task_assignments: dict) -> None:
        task_ids = []
        for agent_id, instruction in task_assignments.items():
            task_id = f"{job_id}:{agent_id}"
            task_ids.append(task_id)
            self.redis.set(f"task:{task_id}:job_id", job_id, ex=3600)
            self.dispatch_to_agent(agent_id, instruction, task_id)

        self.redis.hset(f"job:{job_id}", mapping={
            "task_ids": json.dumps(task_ids),
            "status": "in_progress",
            "created_at": str(time.time())
        })
        self.redis.expire(f"job:{job_id}", 3600)

        # Check immediately in case all tools resolved instantly
        asyncio.ensure_future(self._async_check_job(job_id))
        logger.info(f"Job {job_id} created with {len(task_ids)} tasks")

    async def _async_check_job(self, job_id: str):
        await asyncio.sleep(5)  # give tools time to complete
        self._check_job_complete(job_id)

    def receive_report(self, text: str, agent_id: str) -> bool:
        if not text.startswith("REPORT:"):
            return False
        parts = text.split(":", 2)
        if len(parts) < 3:
            return False
        task_id = parts[1]
        report_body = parts[2].strip()
        self.redis.hset(f"task:{task_id}", mapping={
            "status": "complete",
            "report": report_body,
            "completed_at": str(time.time())
        })
        job_id = self.redis.get(f"task:{task_id}:job_id")
        if job_id:
            self._check_job_complete(job_id)
        return True

    def _check_job_complete(self, job_id: str) -> None:
        job = self.redis.hgetall(f"job:{job_id}")
        if not job or job.get("status") == "complete":
            return
        task_ids = json.loads(job.get("task_ids", "[]"))
        reports = {}
        all_done = True
        for task_id in task_ids:
            task = self.redis.hgetall(f"task:{task_id}")
            if task.get("status") != "complete":
                all_done = False
                break
            agent_name = task.get("agent_name", task_id)
            report = task.get("report", "(no report)")
            tool_fired = task.get("tool_fired", "none")
            reports[agent_name] = {"report": report, "tool": tool_fired}

        if all_done:
            asyncio.ensure_future(self._send_final_report(job_id, reports))
            self.redis.hset(f"job:{job_id}", "status", "complete")

    async def _send_final_report(self, job_id: str, reports: dict) -> None:
        lines = [f"<b>✅ Mission Complete — Job {job_id.split('_', 1)[-1]}</b>\n"]
        for agent_name, data in reports.items():
            tool = data.get('tool', 'none')
            report = data.get('report', '')
            # Truncate long JSON tool outputs
            try:
                parsed = json.loads(report)
                report_text = json.dumps(parsed, indent=2)[:800]
            except Exception:
                report_text = report[:800]
            tool_tag = f" <i>[via {tool}]</i>" if tool != 'none' else ""
            lines.append(f"<b>{agent_name}{tool_tag}:</b>\n<code>{report_text}</code>\n")

        lines.append("<b>Simon:</b> All tasks complete. Standing by.")
        full_report = "\n".join(lines)

        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                TELEGRAM_API.format(token=self.simon_token, method="sendMessage"),
                json={"chat_id": self.serge_chat_id, "text": full_report, "parse_mode": "HTML"}
            )
        logger.info(f"Final report for job {job_id} sent to Serge")

    def register_agent_chat(self, agent_id: str, chat_id: str) -> None:
        self.redis.set(f"agent:{agent_id}:serge_chat_id", chat_id, ex=86400 * 30)
        logger.info(f"Registered chat_id {chat_id} for {agent_id}")
