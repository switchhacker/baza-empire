"""
Baza Empire — Agent Event Bus
Real-time inter-agent communication via Redis pub/sub.

Events allow agents to react to changes without polling:
- research_complete: Scout finished research, results available
- task_created: New task assigned, may need research
- task_completed: Agent finished a task
- knowledge_updated: empire_knowledge changed
- context_invalidated: Agent should refresh its context

Usage:
    from core.event_bus import EventBus

    bus = EventBus(agent_id="scout_reeves")

    # Publishing (non-blocking)
    await bus.publish("research_complete", {
        "topic": "PA HIC permit requirements",
        "artifact": "pa_hic_requirements.md",
        "summary": "PA requires HIC license for residential work over $500..."
    })

    # Subscribing (in agent's main loop)
    async for event in bus.listen("task_created", "knowledge_updated"):
        if event["type"] == "task_created":
            # Check if this task needs research
            ...
"""
import asyncio
import json
import logging
import os
import time
from typing import AsyncGenerator, Optional

import redis.asyncio as aioredis

logger = logging.getLogger("baza.event_bus")

REDIS_URL = os.environ.get("BAZA_REDIS_URL", "redis://localhost:6379/1")  # DB 1 for events (DB 0 is chat history)

# Event channels
CHANNELS = {
    "research_complete": "baza:events:research_complete",
    "task_created": "baza:events:task_created",
    "task_completed": "baza:events:task_completed",
    "task_blocked": "baza:events:task_blocked",
    "knowledge_updated": "baza:events:knowledge_updated",
    "context_invalidated": "baza:events:context_invalidated",
    "dispatch": "baza:events:dispatch",
    "agent_alert": "baza:events:agent_alert",
    "agent_help_request": "baza:events:agent_help_request",
    "agent_help_response": "baza:events:agent_help_response",
    "report_generated": "baza:events:report_generated",
}

# Global channel for all events
ALL_EVENTS = "baza:events:*"


class AgentEvent:
    """Structured event payload."""

    def __init__(self, event_type: str, source_agent: str, data: dict, timestamp: float = None):
        self.type = event_type
        self.source = source_agent
        self.data = data
        self.timestamp = timestamp or time.time()

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type,
            "source": self.source,
            "data": self.data,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, raw: str) -> "AgentEvent":
        d = json.loads(raw)
        return cls(d["type"], d["source"], d["data"], d.get("timestamp"))

    def __repr__(self):
        return f"<AgentEvent {self.type} from={self.source} data_keys={list(self.data.keys())}>"


class EventBus:
    """Redis-backed event bus for inter-agent communication."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    async def publish(self, event_type: str, data: dict):
        """Publish an event. Non-blocking, fire-and-forget."""
        try:
            r = await self._get_redis()
            channel = CHANNELS.get(event_type, f"baza:events:{event_type}")
            event = AgentEvent(event_type, self.agent_id, data)
            await r.publish(channel, event.to_json())

            # Also store in a recent events list for agents that missed it
            await r.lpush("baza:recent_events", event.to_json())
            await r.ltrim("baza:recent_events", 0, 99)  # Keep last 100

            logger.info(f"[{self.agent_id}] Published {event_type}: {list(data.keys())}")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Failed to publish {event_type}: {e}")

    async def listen(self, *event_types: str) -> AsyncGenerator[AgentEvent, None]:
        """Subscribe to event types and yield events as they arrive."""
        try:
            r = await self._get_redis()
            pubsub = r.pubsub()
            channels = [CHANNELS.get(et, f"baza:events:{et}") for et in event_types]
            await pubsub.subscribe(*channels)
            logger.info(f"[{self.agent_id}] Listening on: {event_types}")

            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        event = AgentEvent.from_json(message["data"])
                        if event.source != self.agent_id:  # Don't process own events
                            yield event
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Bad event data: {e}")
        except Exception as e:
            logger.error(f"[{self.agent_id}] Listen error: {e}")

    async def get_recent_events(self, limit: int = 20, since: float = None) -> list:
        """Get recent events (for agents that just came online or need to catch up)."""
        try:
            r = await self._get_redis()
            raw_events = await r.lrange("baza:recent_events", 0, limit - 1)
            events = []
            for raw in raw_events:
                try:
                    event = AgentEvent.from_json(raw)
                    if since and event.timestamp < since:
                        continue
                    if event.source != self.agent_id:
                        events.append(event)
                except Exception:
                    continue
            return events
        except Exception as e:
            logger.error(f"[{self.agent_id}] get_recent_events error: {e}")
            return []

    async def alert_agent(self, target_agent: str, message: str, data: dict = None):
        """Send a targeted alert to a specific agent."""
        await self.publish("agent_alert", {
            "target": target_agent,
            "message": message,
            **(data or {}),
        })

    async def close(self):
        if self._redis:
            await self._redis.close()
            self._redis = None


# ── Convenience functions for non-async contexts ─────────────────────────────

def publish_sync(agent_id: str, event_type: str, data: dict):
    """Synchronous publish for use in skills and non-async code."""
    import redis
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        channel = CHANNELS.get(event_type, f"baza:events:{event_type}")
        event = AgentEvent(event_type, agent_id, data)
        r.publish(channel, event.to_json())
        r.lpush("baza:recent_events", event.to_json())
        r.ltrim("baza:recent_events", 0, 99)
        r.close()
    except Exception as e:
        logger.error(f"Sync publish failed: {e}")


def get_recent_events_sync(agent_id: str, limit: int = 20) -> list:
    """Synchronous recent events fetch."""
    import redis
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        raw = r.lrange("baza:recent_events", 0, limit - 1)
        r.close()
        events = []
        for item in raw:
            try:
                e = AgentEvent.from_json(item)
                if e.source != agent_id:
                    events.append(e)
            except Exception:
                continue
        return events
    except Exception:
        return []
