"""
Baza Empire — Nova Web Router
------------------------------
FastAPI router that powers the ahb123.com chat widget and intake forms.

Endpoints:
  POST /nova/chat      — conversational reply from Nova's model
  POST /api/leads      — create a lead from the Plan/Contact form or Nova widget,
                          Telegram-alerts Rex (hot/warm) based on lead scoring

Mounted into tools/server.py via `app.include_router(nova_router)`.
Exposed publicly through Caddy reverse-proxy on nova.ahb123.com (see
dashboard/artifacts/proj-ahb123/sq_bundle/nova-widget/Caddyfile).
"""
from __future__ import annotations

import os
import re
import json
import time
import uuid
import sqlite3
import logging
import asyncio
from datetime import datetime
from typing import Optional, List

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.ollama_client import chat_stream_pooled

logger = logging.getLogger("nova_router")

# ─── Configuration ────────────────────────────────────────────────────────────

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSONA_DIR = os.path.join(FRAMEWORK_DIR, "agents", "nova_sterling", "persona")
DB_PATH = os.path.join(FRAMEWORK_DIR, "dashboard", "baza_projects.db")
CHATBOT_CONFIG = os.path.join(
    FRAMEWORK_DIR, "dashboard", "artifacts", "proj-ahb123", "simon_chatbot_config.md"
)
NOVA_MODEL = os.environ.get("NOVA_MODEL", "llama3.1:8b")

# Service area counties (lowercase)
SERVICE_AREA = {
    "philadelphia", "philly", "philadelphia county",
    "montgomery", "montgomery county", "montco",
    "delaware county", "delco", "delaware",
    "bucks", "bucks county", "chester", "chester county",
}
SERVICE_NEIGHBORHOODS = {
    "fishtown", "kensington", "port richmond", "northern liberties", "manayunk",
    "roxborough", "east falls", "chestnut hill", "mount airy", "mt airy",
    "germantown", "center city", "south philly", "south philadelphia",
    "narberth", "ardmore", "jenkintown", "conshohocken", "lansdale",
    "king of prussia", "kop", "norristown", "abington",
    "upper darby", "drexel hill", "media", "springfield", "havertown",
    "wayne", "newtown square", "aston",
    "bensalem", "doylestown", "newtown", "langhorne", "yardley",
}

# ─── Persona loader (cached) ──────────────────────────────────────────────────

_SYSTEM_PROMPT_CACHE: dict = {"text": None, "ts": 0}
_SYSTEM_PROMPT_TTL = 300  # 5 min


def _load_persona() -> str:
    """Concatenate Nova's persona files into a single system prompt."""
    parts = []
    for fname in ("IDENTITY.md", "SOUL.md", "MISSION.md", "USER.md"):
        p = os.path.join(PERSONA_DIR, fname)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                parts.append(f.read().strip())
    # Append web-widget-specific rules
    parts.append(
        "\n\n== WEB WIDGET RULES ==\n"
        "You are on the AHB123.com website chat widget. Homeowners and prospects can message you here.\n"
        "Always reply as plain text — no markdown, no headers, no code blocks.\n"
        "Keep replies short and warm — 1 to 3 sentences per turn. Ask one question at a time.\n"
        "When you have enough information (name, phone or email, project type, general location, budget range),\n"
        "offer to have a project consultant call them back. Do not try to schedule yourself.\n"
        "Never share internal company info, employee details, or admit you are an AI.\n"
        "If someone is abusive or clearly off-topic, disengage politely.\n"
        "The company phone is (800) 484-6404 and email is info@ahb123.com — give these only when asked.\n"
        "== END WEB RULES =="
    )
    return "\n\n".join(parts)


def system_prompt() -> str:
    now = time.time()
    if _SYSTEM_PROMPT_CACHE["text"] and (now - _SYSTEM_PROMPT_CACHE["ts"]) < _SYSTEM_PROMPT_TTL:
        return _SYSTEM_PROMPT_CACHE["text"]
    txt = _load_persona()
    _SYSTEM_PROMPT_CACHE["text"] = txt
    _SYSTEM_PROMPT_CACHE["ts"] = now
    return txt


# ─── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="", tags=["nova"])


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(..., max_length=64)
    message: str = Field(..., max_length=2000)
    history: List[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    session_id: str


class LeadRequest(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    project_type: str = ""
    budget_range: str = ""
    timeline: str = ""
    project_address: str = ""
    project_details: str = ""
    source: str = "nova_web"
    session_id: Optional[str] = None
    submitted_at: Optional[str] = None


class LeadResponse(BaseModel):
    success: bool
    client_id: Optional[str] = None
    temperature: Optional[str] = None
    error: Optional[str] = None


# ─── /nova/chat ───────────────────────────────────────────────────────────────


def _run_ollama(messages: list, sys_prompt: str) -> str:
    """Synchronous wrapper around chat_stream_pooled. Returns full text."""
    chunks: list = []
    try:
        for chunk in chat_stream_pooled(
            model=NOVA_MODEL,
            messages=messages,
            system_prompt=sys_prompt,
            agent_id="nova_sterling_web",
        ):
            chunks.append(chunk)
    except Exception as e:
        logger.exception("Ollama inference failed")
        raise HTTPException(502, f"LLM backend error: {e}")
    return "".join(chunks).strip()


@router.post("/nova/chat", response_model=ChatResponse)
async def nova_chat(req: ChatRequest, request: Request):
    """Run one turn of Nova chat. Stateless — client sends history."""
    # Build messages from client-provided history + current user turn
    msgs = []
    for m in req.history[-12:]:  # cap to 12 prior turns
        if m.role in ("user", "assistant"):
            msgs.append({"role": m.role, "content": m.content[:1500]})
    msgs.append({
        "role": "user",
        "content": (
            f"{req.message}\n\n"
            "[Reply naturally as Nova Sterling — warm, professional, plain text. "
            "1-3 sentences. Ask ONE question if qualifying.]"
        ),
    })

    loop = asyncio.get_event_loop()
    reply = await loop.run_in_executor(None, _run_ollama, msgs, system_prompt())

    if not reply:
        reply = (
            "I'm here — tell me a little about what you're thinking and I can point you "
            "in the right direction."
        )

    # Best-effort: append session log to knowledge DB (fire-and-forget)
    try:
        _log_chat(req.session_id, req.message, reply)
    except Exception as e:
        logger.warning(f"chat log failed: {e}")

    return ChatResponse(reply=reply, session_id=req.session_id)


def _log_chat(session_id: str, user_msg: str, bot_reply: str):
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS nova_web_chats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
            "user_msg TEXT, bot_reply TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO nova_web_chats (session_id, user_msg, bot_reply, created_at) VALUES (?,?,?,?)",
            (session_id, user_msg[:2000], bot_reply[:2000], datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


# ─── /api/leads ───────────────────────────────────────────────────────────────


def _classify_lead(lead: LeadRequest) -> str:
    """Return hot / warm / cold based on Nova's lead-temp rules."""
    # Normalize
    loc = (lead.project_address or "").lower()
    in_area = any(k in loc for k in SERVICE_AREA) or any(k in loc for k in SERVICE_NEIGHBORHOODS)
    # If no address given, assume in-area (don't penalize missing field)
    if not loc.strip():
        in_area = True

    budget_hot = lead.budget_range in {"25k_50k", "50k_100k", "100k_plus", "10k_25k"}
    budget_warm = lead.budget_range in {"not_sure", "10k_25k"}
    budget_low = lead.budget_range == "under_10k"

    timeline_hot = lead.timeline in {"asap", "1_3_months"}
    timeline_warm = lead.timeline in {"3_6_months"}
    timeline_cold = lead.timeline in {"exploring", "6_plus"}

    if budget_low or not in_area:
        return "cold"
    if budget_hot and in_area and timeline_hot:
        return "hot"
    if (budget_warm or budget_hot) and in_area and not timeline_cold:
        return "warm"
    return "warm"  # default when we have any contact info


def _sanitize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return phone


def _save_lead(lead: LeadRequest, temp: str) -> str:
    cid = uuid.uuid4().hex[:24]
    conn = sqlite3.connect(DB_PATH, timeout=5)
    try:
        notes_parts = []
        if lead.project_details:
            notes_parts.append(f"Details: {lead.project_details[:1500]}")
        if lead.project_type:
            notes_parts.append(f"Type: {lead.project_type}")
        if lead.budget_range:
            notes_parts.append(f"Budget: {lead.budget_range}")
        if lead.timeline:
            notes_parts.append(f"Timeline: {lead.timeline}")
        notes_parts.append(f"Source: {lead.source}")
        notes_parts.append(f"Temp: {temp}")
        if lead.session_id:
            notes_parts.append(f"Session: {lead.session_id}")
        notes = " | ".join(notes_parts)

        conn.execute(
            """INSERT INTO ahb_clients
               (id, name, phone, email, address, city, source, status, notes, assigned_agent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid,
                lead.full_name or "Unknown",
                _sanitize_phone(lead.phone),
                lead.email,
                lead.project_address,
                "Philadelphia",
                lead.source or "nova_web",
                "lead",
                notes,
                "rex_valor" if temp in ("hot", "warm") else "",
            ),
        )
        conn.commit()
        return cid
    finally:
        conn.close()


def _telegram_alert(lead: LeadRequest, cid: str, temp: str):
    token = os.environ.get("TELEGRAM_REX_VALOR") or os.environ.get("TELEGRAM_REX_SIMMONS")
    chat_id = os.environ.get("REX_CHAT_ID") or os.environ.get("SERGE_CHAT_ID")
    if not token or not chat_id:
        logger.info(f"[lead {cid}] no Telegram token/chat_id — skipping alert")
        return
    icon = {"hot": "🔥", "warm": "🟠", "cold": "🔵"}.get(temp, "•")
    body = (
        f"{icon} New {temp.upper()} lead — {lead.source}\n"
        f"Name: {lead.full_name or '(unknown)'}\n"
        f"Phone: {_sanitize_phone(lead.phone) or '(not given)'}\n"
        f"Email: {lead.email or '(not given)'}\n"
        f"Project: {lead.project_type or '(not specified)'}\n"
        f"Budget: {lead.budget_range or '(not specified)'}\n"
        f"Timeline: {lead.timeline or '(not specified)'}\n"
        f"Address: {lead.project_address or '(not given)'}\n"
        f"---\n"
        f"{(lead.project_details or '')[:500]}\n"
        f"---\n"
        f"Client ID: {cid}"
    )
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": body},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"telegram alert failed: {e}")


@router.post("/api/leads", response_model=LeadResponse)
async def create_lead(lead: LeadRequest):
    # Minimum required: at least one contact channel + something about the project
    if not (lead.phone or lead.email):
        raise HTTPException(400, "Phone or email required")
    if not (lead.full_name or lead.project_details):
        raise HTTPException(400, "Name or project details required")

    temp = _classify_lead(lead)
    try:
        cid = _save_lead(lead, temp)
    except Exception as e:
        logger.exception("save lead failed")
        raise HTTPException(500, f"Database error: {e}")

    # Fire-and-forget Telegram alert
    try:
        _telegram_alert(lead, cid, temp)
    except Exception as e:
        logger.warning(f"alert failed: {e}")

    return LeadResponse(success=True, client_id=cid, temperature=temp)


# ─── CORS helper ──────────────────────────────────────────────────────────────
# Applied by the parent app (tools/server.py). This router adds a specific
# CORS middleware for nova endpoints allowing only ahb123.com origins.

def apply_nova_cors(app):
    """Call once from tools/server.py after include_router."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://ahb123.com",
            "https://www.ahb123.com",
            "http://localhost:3000",  # dev
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )
