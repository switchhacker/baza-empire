"""
Group chat coordinator — decides which agents should respond to a message.
Routes each agent through the GPU pool so responses stream one by one,
each grabbing the next free GPU as soon as it's available.
"""

import re
import threading
from core.gpu_pool import gpu_pool

# Keywords that trigger each agent
AGENT_TRIGGERS = {
    "brad_gant": [
        "infrastructure", "research", "intel", "server", "network", "hardware",
        "data", "analysis", "investigate", "find out", "look into", "specs",
        "performance", "monitoring", "brad"
    ],
    "simon_bately": [
        "business", "client", "customer", "invoice", "payment", "payroll",
        "website", "marketing", "email", "call", "meeting", "proposal",
        "contract", "project", "schedule", "ahb123", "allhome", "simon"
    ],
    "claw_batto": [
        "code", "build", "deploy", "install", "linux", "docker", "git",
        "script", "bug", "fix", "database", "api", "server setup", "devops",
        "python", "javascript", "node", "npm", "claw"
    ],
    "phil_hass": [
        "legal", "law", "compliance", "contract", "liability", "tax",
        "finance", "accounting", "regulation", "risk", "insurance",
        "llc", "incorporate", "permit", "license", "phil"
    ]
}

# Agent display names for group chat headers
AGENT_DISPLAY_NAMES = {
    "brad_gant": "Brad Gant",
    "simon_bately": "Simon Bately",
    "claw_batto": "Claw Batto",
    "phil_hass": "Phil Hass",
}


def should_agent_respond(agent_id: str, message: str, is_group: bool) -> bool:
    """
    In a private chat, always respond.
    In a group chat, only respond if message is relevant to the agent.
    """
    if not is_group:
        return True

    message_lower = message.lower()

    # Always respond if directly mentioned by name
    agent_name = agent_id.replace("_", " ").lower()
    if agent_name in message_lower:
        return True

    # Check keyword triggers
    triggers = AGENT_TRIGGERS.get(agent_id, [])
    for trigger in triggers:
        if trigger in message_lower:
            return True

    return False


def get_relevant_agents(message: str, all_agents: list) -> list:
    """Return list of agent IDs that should respond to this message in a group."""
    relevant = []
    for agent_id in all_agents:
        if should_agent_respond(agent_id, message, is_group=True):
            relevant.append(agent_id)

    # If no specific agent matched, default to brad_gant as coordinator
    if not relevant:
        relevant = ["brad_gant"]

    return relevant


def run_group_responses(agents: list, run_agent_fn, message: str,
                        send_fn, history: list = None):
    """
    Run multiple agents sequentially through the GPU pool.
    Each agent waits for a free GPU, streams its response, then releases.
    Responses are sent one by one as each agent finishes.

    Args:
        agents: list of agent_id strings to run
        run_agent_fn: callable(agent_id, message, history) -> str
        message: the user message
        send_fn: callable(agent_id, text) — sends the response to the user
        history: shared conversation history
    """
    def run_one(agent_id):
        try:
            response = run_agent_fn(agent_id, message, history or [])
            name = AGENT_DISPLAY_NAMES.get(agent_id, agent_id)
            send_fn(agent_id, response)
        except Exception as e:
            send_fn(agent_id, f"_(error: {str(e)})_")

    # Run agents sequentially — each blocks on GPU pool internally
    # Two can run in parallel if both GPUs are free
    threads = []
    for agent_id in agents:
        t = threading.Thread(target=run_one, args=(agent_id,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()


def build_group_context(history: list, current_task: str) -> str:
    """Build context string for group chat so agents know what's been discussed."""
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
    """Check if an agent signaled task completion."""
    return "TASK_COMPLETE" in response.upper()


def gpu_status() -> str:
    """Return a readable GPU pool status string."""
    slots = gpu_pool.status()
    lines = ["GPU Pool:"]
    for s in slots:
        status = f"🟢 {s['agent']}" if s['in_use'] else "⚪ free"
        lines.append(f"  GPU{s['id']} ({s['name']}): {status}")
    return "\n".join(lines)
