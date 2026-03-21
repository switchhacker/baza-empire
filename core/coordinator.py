"""
Group chat coordinator — decides which agents should respond to a message.
"""

import re
import threading
from core.gpu_pool import gpu_pool

# Keywords that trigger each agent
# Rules:
#   - Agent name always triggers them
#   - Keywords must be specific enough to avoid false positives
#   - Phil should NOT respond to general "skills" or tech questions
AGENT_TRIGGERS = {
    "brad_gant": [
        "infrastructure", "research", "intel", "server", "network", "hardware",
        "specs", "performance", "monitoring", "brad", "gant"
    ],
    "simon_bately": [
        "business", "client", "customer", "invoice", "payment", "payroll",
        "website", "marketing", "proposal", "leads", "lead generation",
        "schedule", "allhome", "ahbco", "simon", "bately"
    ],
    "claw_batto": [
        "code", "build", "deploy", "install", "linux", "docker", "git",
        "script", "bug", "fix", "database", "api", "devops",
        "python", "javascript", "node", "npm", "claw", "batto"
    ],
    "phil_hass": [
        "legal", "law", "compliance", "liability", "tax",
        "accounting", "regulation", "risk", "insurance",
        "llc", "incorporate", "permit", "license", "gdpr", "ccpa",
        "phil", "hass"
    ]
}

AGENT_DISPLAY_NAMES = {
    "brad_gant": "Brad Gant",
    "simon_bately": "Simon Bately",
    "claw_batto": "Claw Batto",
    "phil_hass": "Phil Hass",
}


def should_agent_respond(agent_id: str, message: str, is_group: bool) -> bool:
    """
    In a private chat, always respond.
    In a group chat, only respond if the message is relevant to this agent.
    """
    if not is_group:
        return True

    message_lower = message.lower()

    # Check keyword triggers (whole-word match to avoid partial hits)
    triggers = AGENT_TRIGGERS.get(agent_id, [])
    for trigger in triggers:
        # Use word boundary matching for single words, substring for phrases
        if " " in trigger:
            if trigger in message_lower:
                return True
        else:
            if re.search(rf"\b{re.escape(trigger)}\b", message_lower):
                return True

    return False


def get_relevant_agents(message: str, all_agents: list) -> list:
    relevant = []
    for agent_id in all_agents:
        if should_agent_respond(agent_id, message, is_group=True):
            relevant.append(agent_id)

    if not relevant:
        relevant = ["brad_gant"]

    return relevant


def run_group_responses(agents: list, run_agent_fn, message: str,
                        send_fn, history: list = None):
    def run_one(agent_id):
        try:
            response = run_agent_fn(agent_id, message, history or [])
            send_fn(agent_id, response)
        except Exception as e:
            send_fn(agent_id, f"_(error: {str(e)})_")

    threads = []
    for agent_id in agents:
        t = threading.Thread(target=run_one, args=(agent_id,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()


def build_group_context(history: list, current_task: str) -> str:
    context = ""
    if current_task:
        context += f"Current task: {current_task}\n\n"
    if history:
        context += "Recent conversation:\n"
        for msg in history[-10:]:
            agent = msg.get("agent", "user")
            context += f"{agent}: {msg['content']}\n"
    return context


def is_task_complete(response: str) -> bool:
    return "TASK_COMPLETE" in response.upper()


def gpu_status() -> str:
    slots = gpu_pool.status()
    lines = ["GPU Pool:"]
    for s in slots:
        status = f"🟢 {s['agent']}" if s['in_use'] else "⚪ free"
        lines.append(f"  GPU{s['id']} ({s['name']}): {status}")
    return "\n".join(lines)
