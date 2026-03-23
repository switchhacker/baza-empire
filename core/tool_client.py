"""
Baza Empire — Tool Client
--------------------------
Used by agents to call the tool server.
Simon uses this to call tools on behalf of the team.
Other agents use this to execute their own tools directly.
"""

import httpx
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

TOOL_SERVER_URL = "http://localhost:8000"


class ToolClient:
    def __init__(self, base_url: str = TOOL_SERVER_URL):
        self.base_url = base_url

    async def call(self, agent: str, tool: str, input_data: dict = {},
                   task_id: Optional[str] = None) -> dict:
        """
        Call a tool on the tool server.
        agent: claw_batto, phil_hass, sam_axe, simon_bately
        tool: the tool name (e.g. run-command, generate-invoice)
        """
        agent_slug = agent.replace("_", "-").replace("batto", "batto") \
            .replace("simon_bately", "simon").replace("claw_batto", "claw") \
            .replace("phil_hass", "phil").replace("sam_axe", "sam")

        # normalize agent slug
        slug_map = {
            "simon_bately": "simon",
            "claw_batto": "claw",
            "phil_hass": "phil",
            "sam_axe": "sam"
        }
        slug = slug_map.get(agent, agent)

        url = f"{self.base_url}/tools/{slug}/{tool}"
        payload = {"input": input_data}
        if task_id:
            payload["task_id"] = task_id

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                result = resp.json()
                logger.info(f"Tool {slug}/{tool} → success={result.get('success')} "
                            f"({result.get('duration_ms')}ms)")
                return result
        except Exception as e:
            logger.error(f"Tool call failed {slug}/{tool}: {e}")
            return {"success": False, "error": str(e), "tool": f"{slug}/{tool}"}

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def list_tools(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/tools")
                return resp.json()
        except Exception:
            return {}


# Singleton
tool_client = ToolClient()
