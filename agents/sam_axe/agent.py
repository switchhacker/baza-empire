"""
Baza Empire — Sam Axe
Imaging, Graphics, Media, Architectural & Engineering Visualization

OVERHAUL: Full image pipeline with analysis → description → edit → 2 variants workflow.
When user sends a photo:
1. Sam analyzes every object, viewpoint, geometry, material, color
2. Sends detailed description + awaits edit instructions
3. User sends natural language changes ("add white cabinets", "paint walls blue")
4. Sam generates 2 variants via img2img, labeled "1st try" / "2nd try"
5. User picks one, can request further edits
6. All context is persisted per image — re-sending a worked-on image skips analysis
"""
import os
import re
import json
import time
import asyncio
import logging
import tempfile
import hashlib
from pathlib import Path
from telegram import Update, InputFile, InputMediaPhoto
from telegram.constants import ChatAction
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from core.base_agent import BaseAgent
from core.memory import save_message, get_history
from dashboard.private_inbound import inbound_dir, write_attachment_meta

logger = logging.getLogger(__name__)

IMAGE_KEYWORDS = [
    "generate", "create", "draw", "render", "design",
    "logo", "brand", "visual", "blueprint", "floor plan", "elevation",
    "architectural", "engineering", "diagram", "sketch", "concept", "art",
    "illustration", "banner", "poster", "mockup", "3d render",
]

EDIT_KEYWORDS = [
    "add", "change", "paint", "install", "replace", "remove", "modify",
    "make it", "switch", "update", "swap", "put", "place", "set",
    "lighter", "darker", "bigger", "smaller", "more", "less",
    "different", "new", "white", "black", "blue", "green", "red", "wood",
    "marble", "granite", "tile", "modern", "rustic", "farmhouse",
]

SHOW_PHRASES = [
    "show me", "send me", "show the", "send the", "show it", "see it",
    "view it", "display", "see what you made", "let me see",
]

# Triggers that opt INTO the heavy architectural / room analysis on an inbound
# photo. Without one of these in the caption, Sam stashes the photo quietly
# (private) and waits for instructions instead of describing the room. Serge
# was getting unsolicited "I see a kitchen with cabinets" replies during
# reference-photo uploads — analysis is now opt-in.
ANALYZE_TRIGGER_KEYWORDS = (
    "analyze", "analyse", "describe", "what is this", "what's this",
    "what do you see", "what do u see", "tell me about this",
    "scan this", "read this",
)

# ── Character / avatar reference intake ────────────────────────────────────
# When a photo's caption matches one of these triggers, Sam treats it as a
# CHARACTER REFERENCE (face, wardrobe, mood, etc.) instead of a room photo.
# Multiple refs accumulate into a "mood board" Sam can paint from.
REF_TRIGGER_KEYWORDS = (
    "ref", "reference", "avatar", "character", "persona", "mood board",
    "moodboard", "for cole", "for sam", "for the avatar",
)

REF_ROLE_KEYWORDS = {
    "face":     ("face", "head", "selfie", "headshot", "portrait", "mug", "visage"),
    "body":     ("body", "build", "physique", "frame", "torso", "stance"),
    "hair":     ("hair", "haircut", "hairstyle"),
    "wardrobe": ("wardrobe", "outfit", "clothing", "clothes", "suit", "jacket",
                 "shirt", "coat", "dress", "fit"),
    "pose":     ("pose", "posture", "gesture"),
    "lighting": ("lighting", "light", "lit", "illumination"),
    "style":    ("style", "aesthetic", "vibe", "mood", "tone", "palette"),
    "setting":  ("setting", "environment", "background", "scene", "location"),
}

PORTRAIT_ANALYSIS_PROMPT = (
    "Analyze this image as a CHARACTER REFERENCE for portrait painting. "
    "Be precise and exhaustive. Cover:\n"
    "1. FACE: shape, jaw, cheekbones, chin, brow, nose, lips, ears, age cues\n"
    "2. EYES: shape, color, set, gaze, brow expression\n"
    "3. HAIR: color, length, texture, style, hairline, facial hair\n"
    "4. SKIN: tone, complexion, distinctive marks, scars, freckles\n"
    "5. BUILD: apparent age, body type, posture, height cues\n"
    "6. WARDROBE: every garment — fabric, color, fit, era, accessories\n"
    "7. EXPRESSION & MOOD: emotion, energy, gaze direction\n"
    "8. STYLE & ENVIRONMENT: lighting, color palette, backdrop, vibe\n\n"
    "Output ONE paragraph of dense prose, suitable for direct injection into "
    "a Stable Diffusion prompt. No headers, no lists — just the paragraph."
)

AVATAR_TRIGGERS = (
    "from refs", "from the refs", "use the refs", "use refs", "use the references",
    "from references", "with the refs", "based on the refs", "based on refs",
    "from these", "from those", "from the photos", "from the images",
    "make the avatar", "build the avatar", "paint the avatar",
)

MAX_HISTORY = 10
CONTEXT_DIR = "/mnt/empirepool/media/generated/.context"
OUTPUT_DIR = "/mnt/empirepool/media/generated"


class SamAxe(BaseAgent):
    AGENT_ID = "sam_axe"
    MODEL = "qwen3-vl:latest"
    TOKEN_ENV = "TELEGRAM_SAM_AXE"
    USE_GPU_POOL = True

    _PENDING_EDITS_FILE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".pending_edits.json"
    )

    def __init__(self):
        super().__init__()
        self._session_images: dict = {}  # {chat_id: [path, ...]}
        self._pending_edits: dict = self._load_pending_edits()
        # Burst debounce — Serge often pastes 5-30 photos in a row. Each one
        # used to trigger a separate ack. Now we accumulate per chat and emit
        # ONE summary message _BURST_WINDOW seconds after the last arrival.
        self._photo_burst: dict = {}  # {chat_id: {"files": [...], "job": Job|None}}
        self._BURST_WINDOW = 5.0  # seconds of inter-photo silence to flush

    def _load_pending_edits(self) -> dict:
        """Load pending edits from disk (survives restarts)."""
        try:
            if os.path.exists(self._PENDING_EDITS_FILE):
                with open(self._PENDING_EDITS_FILE) as f:
                    raw = json.load(f)
                # Keys are strings in JSON — convert back to int chat_ids
                return {int(k): v for k, v in raw.items()}
        except Exception:
            pass
        return {}

    def _save_pending_edits(self):
        """Persist pending edits to disk."""
        try:
            with open(self._PENDING_EDITS_FILE, "w") as f:
                json.dump(self._pending_edits, f)
        except Exception:
            pass

    # ── Character reference helpers ───────────────────────────────────────────

    def _is_reference_upload(self, caption: str) -> bool:
        if not caption:
            return False
        c = caption.lower()
        return any(t in c for t in REF_TRIGGER_KEYWORDS)

    def _detect_ref_role(self, caption: str) -> str:
        c = (caption or "").lower()
        for role, keys in REF_ROLE_KEYWORDS.items():
            if any(k in c for k in keys):
                return role
        return "general"

    def _add_reference(self, chat_id: int, path: str, role: str, description: str, caption: str):
        slot = self._pending_edits.setdefault(chat_id, {})
        refs = slot.setdefault("references", [])
        refs.append({
            "path": path,
            "role": role,
            "caption": caption,
            "description": description,
        })
        self._save_pending_edits()

    def _has_references(self, chat_id: int) -> bool:
        return bool(self._pending_edits.get(chat_id, {}).get("references"))

    def _is_avatar_from_refs_request(self, text: str, chat_id: int) -> bool:
        if not self._has_references(chat_id):
            return False
        t = text.lower()
        if any(trigger in t for trigger in AVATAR_TRIGGERS):
            return True
        if any(kw in t for kw in ("avatar", "character", "persona")) and \
           any(kw in t for kw in ("generate", "make", "paint", "draw", "create", "render")):
            return True
        # Name-match: user says "generate elie" and "elie" appears in any ref caption
        if self._matched_refs_for_text(text, chat_id):
            if any(kw in t for kw in ("generate", "make", "paint", "draw", "create", "render", "show")):
                return True
        return False

    _ROLE_VOCAB = frozenset({
        "ref", "reference", "references", "face", "body", "wardrobe", "style",
        "pose", "lighting", "hair", "mood", "setting", "general", "moodboard",
        "mood-board", "character", "persona", "avatar", "for", "the",
    })

    def _matched_refs_for_text(self, text: str, chat_id: int) -> list:
        """Return refs whose caption contains a non-vocab token from the text."""
        refs = self._pending_edits.get(chat_id, {}).get("references", [])
        if not refs:
            return []
        t_tokens = {tok for tok in re.split(r'[\s:,;\-_/.]+', text.lower()) if len(tok) >= 3}
        if not t_tokens:
            return []
        matched = []
        for r in refs:
            cap_tokens = {tok for tok in re.split(r'[\s:,;\-_/.]+', (r.get("caption") or "").lower())
                          if len(tok) >= 3 and tok not in self._ROLE_VOCAB}
            if cap_tokens & t_tokens:
                matched.append(r)
        return matched

    # ── Image context persistence ─────────────────────────────────────────────

    def _get_image_hash(self, path: str) -> str:
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()[:50000]).hexdigest()[:16]
        except Exception:
            return ""

    def _get_context(self, img_hash: str) -> dict:
        """Load persisted context for an image (skips re-analysis if exists)."""
        os.makedirs(CONTEXT_DIR, exist_ok=True)
        ctx_file = os.path.join(CONTEXT_DIR, f"{img_hash}.json")
        if os.path.exists(ctx_file):
            try:
                with open(ctx_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_context(self, img_hash: str, context: dict):
        os.makedirs(CONTEXT_DIR, exist_ok=True)
        ctx_file = os.path.join(CONTEXT_DIR, f"{img_hash}.json")
        with open(ctx_file, "w") as f:
            json.dump(context, f, indent=2)

    def _record_generated(self, chat_id: int, path: str):
        self._session_images.setdefault(chat_id, [])
        if path not in self._session_images[chat_id]:
            self._session_images[chat_id].append(path)
        self.remember("last_image_generated", path, "images")

    # ── Request classification ────────────────────────────────────────────────

    def _is_show_request(self, text: str) -> bool:
        t = text.lower()
        if re.search(r'/mnt/\S+\.(?:png|jpg|jpeg|webp)', text):
            return True
        return any(phrase in t for phrase in SHOW_PHRASES)

    def _is_image_request(self, text: str) -> bool:
        if self._is_show_request(text):
            return False
        t = text.lower()
        if any(kw in t for kw in IMAGE_KEYWORDS):
            return True
        # Also catch edit-like requests when no pending edit context exists
        # (user describes a room renovation — treat as generation from scratch)
        edit_hit = sum(1 for kw in EDIT_KEYWORDS if kw in t)
        if edit_hit >= 2 and len(text) > 30:
            return True
        return False

    def _is_edit_request(self, text: str, chat_id: int) -> bool:
        """Is this an edit instruction for a pending image?"""
        if chat_id not in self._pending_edits:
            return False
        t = text.lower()
        if text.strip() in ("1", "2", "3", "1st", "2nd", "3rd", "first", "second", "third"):
            return False
        # An explicit "generate / create / draw / render" intent is a NEW image,
        # not an edit — let _is_image_request handle it.
        if any(kw in t for kw in IMAGE_KEYWORDS):
            return False
        return any(kw in t for kw in EDIT_KEYWORDS)

    def _is_variant_selection(self, text: str, chat_id: int) -> bool:
        """Is this picking variant 1, 2, or 3?"""
        if chat_id not in self._pending_edits:
            return False
        t = text.strip().lower()
        return t in ("1", "2", "3", "1st", "2nd", "3rd", "first", "second", "third",
                      "option 1", "option 2", "option 3",
                      "try 1", "try 2", "try 3",
                      "1st try", "2nd try", "3rd try")

    def _get_variant_choice(self, text: str) -> int:
        t = text.strip().lower()
        if any(k in t for k in ("3", "3rd", "third")):
            return 3
        if any(k in t for k in ("2", "2nd", "second")):
            return 2
        return 1

    # ── Send images with labels ───────────────────────────────────────────────

    async def _send_variants(self, chat_id: int, paths: list, context_obj, edit_request: str = ""):
        """Send up to 3 variants as a media group."""
        labels = ["1st try", "2nd try", "3rd try", "4th try"]
        media = []
        for i, path in enumerate(paths[:3]):
            if not Path(path).exists():
                continue
            label = labels[i] if i < len(labels) else f"#{i+1}"
            caption = f"🎨 {label}"
            if i == 0 and edit_request:
                caption += f"\n✏️ {edit_request[:100]}"
            with open(path, "rb") as f:
                media.append(InputMediaPhoto(
                    media=f.read(),
                    caption=caption,
                ))
        if media:
            try:
                await context_obj.bot.send_media_group(chat_id=chat_id, media=media)
            except Exception as e:
                # Fallback: send individually
                for i, path in enumerate(paths[:2]):
                    if Path(path).exists():
                        try:
                            with open(path, "rb") as f:
                                await context_obj.bot.send_photo(
                                    chat_id=chat_id,
                                    photo=InputFile(f, filename=Path(path).name),
                                    caption=f"🎨 {labels[i]}"
                                )
                        except Exception:
                            pass

    # ── Photo burst-batch helpers ─────────────────────────────────────────────
    # Serge typically pastes 5-30 photos at a time. Replying to each one
    # spams the chat and hides the useful summary. These helpers accumulate
    # per chat and emit ONE message once the burst goes quiet for
    # _BURST_WINDOW seconds. Implementation is a debounced job on PTB's
    # JobQueue — every new photo cancels the prior pending flush.

    async def _enqueue_photo_burst(self, context, chat_id: int,
                                   filename: str, local_path: str,
                                   caption: str):
        burst = self._photo_burst.setdefault(chat_id, {"files": [], "job": None})
        burst["files"].append({
            "name": filename, "path": local_path,
            "caption": (caption or "")[:80],
            "ts": time.time(),
        })
        # Cancel any pending flush; reschedule to debounce on the latest arrival.
        old_job = burst.get("job")
        if old_job is not None:
            try:
                old_job.schedule_removal()
            except Exception:
                pass
        jq = getattr(context.application, "job_queue", None)
        if jq is None:
            # No JobQueue — fall back to immediate per-photo ack so we don't
            # silently swallow the response.
            files = burst["files"]; burst["files"] = []; burst["job"] = None
            await self._send_response(context.bot, chat_id, f"📥 Saved (private) — {files[-1]['name']}")
            return
        burst["job"] = jq.run_once(
            self._flush_photo_burst, when=self._BURST_WINDOW,
            data={"chat_id": chat_id}, name=f"sam_burst_{chat_id}",
        )

    async def _flush_photo_burst(self, context):
        chat_id = context.job.data.get("chat_id") if context.job else None
        if not chat_id:
            return
        burst = self._photo_burst.get(chat_id)
        if not burst or not burst["files"]:
            return
        files = burst["files"]
        burst["files"] = []
        burst["job"] = None
        n = len(files)
        if n == 1:
            f = files[0]
            msg = (
                f"📥 Added to Data Hub — *{f['name']}*\n"
                f"Pick it from any project's Before/During/After (or any other\n"
                f"image upload) via the 📂 Baza button.\n\n"
                f"Or tell me what to do:\n"
                f"  • `ref: face` / `ref: wardrobe` — add as character reference\n"
                f"  • `analyze` — describe what's in it\n"
                f"  • `paint walls warm gray` (or any edit) — generate variants\n"
                f"  • `send to vault` — move to the private vault\n"
                f"_(SD imaging is paused — generation resumes when the GPU is back.)_"
            )
        else:
            shown = files[:12]
            listing = "\n".join(f"  • {f['name']}" for f in shown)
            more = f"\n  …and {n - 12} more" if n > 12 else ""
            msg = (
                f"📥 OK — added *{n} files* to Data Hub:\n"
                f"{listing}{more}\n\n"
                f"Would you like me to take any action with these?\n"
                f"  • `analyze all` — caption + tag each image (queues them for vision)\n"
                f"  • `ref: wardrobe` (or `ref: face` / `ref: pose`) — treat as character references\n"
                f"  • `attach to <project name> as before/during/after` — file under an AHB project\n"
                f"  • `send all to vault` — move the whole batch into the private vault\n"
                f"  • Or pick them yourself from the AHB project modal via the 📂 Baza button\n"
                f"_(SD imaging is paused. Any images are also queued for vision — they'll be classified when the GPU pool is back.)_"
            )
        save_message(chat_id, self.AGENT_ID, "assistant", msg)
        try:
            await self._send_response(context.bot, chat_id, msg)
        except Exception as e:
            logger.warning(f"[sam_axe] flush_photo_burst send failed: {e}")

    # ── Generic attachment intake (PDFs/video/audio/voice/docs) ───────────────
    # Photos still go through handle_photo (which has the analyze/edit
    # pipeline). Everything else lands in inbound + joins the burst summary
    # so a mixed drop of "10 photos + 2 PDFs + a voice memo" produces ONE
    # ack like "Added 13 files to Data Hub".

    async def handle_attachment_quiet(self, update: Update,
                                      context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        msg = update.message
        if not msg:
            return
        caption = (msg.caption or "").strip()
        logger.info(f"[sam_axe] Attachment received from {chat_id}, caption: {caption[:60]}")

        # Resolve the file object + a sensible filename per Telegram type.
        file_obj = None
        orig_name = None
        kind = "file"
        if msg.document:
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

        import uuid, datetime as _dt
        framework_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        in_dir = inbound_dir(framework_dir, self.AGENT_ID)
        safe = re.sub(r"[^\w.\-_ ()]", "_", orig_name).strip() or "upload"
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Keep the original filename visible — that's what Serge sees in the
        # burst summary. Prefix with timestamp + short uuid to avoid collisions.
        fname = f"{ts}_{uuid.uuid4().hex[:6]}_{safe}"
        local_path = os.path.join(in_dir, fname)
        await file_obj.download_to_drive(local_path)
        write_attachment_meta(local_path, extra={
            "agent_id": self.AGENT_ID,
            "chat_id": chat_id,
            "kind": kind,
            "caption": caption[:500],
            "original_name": orig_name,
            "received_at": _dt.datetime.now().isoformat(),
        })
        save_message(chat_id, self.AGENT_ID, "user", f"[{kind.title()}: {orig_name}] {caption}")
        self.journal("attachment_received",
                     f"{kind}: {orig_name} ({os.path.getsize(local_path)} bytes)",
                     chat_id=chat_id)
        # Roll into the shared burst — Serge gets one summary message, not
        # one per file.
        await self._enqueue_photo_burst(context, chat_id,
                                        orig_name or fname, local_path, caption)

    # ── Photo Handler — the main analysis pipeline ────────────────────────────

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        caption = update.message.caption or ""
        caption_low = caption.lower()

        logger.info(f"[sam_axe] Photo received from {chat_id}, caption: {caption[:60]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        # Download photo (highest res) into the public inbound staging dir
        # so it appears in Data Hub by default. Only files Serge explicitly
        # sends to the vault become private.
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        import uuid, datetime as _dt
        framework_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        in_dir = inbound_dir(framework_dir, self.AGENT_ID)
        filename = f"sam_axe_{_dt.datetime.now().strftime('%Y%m%d_%H%M')}_{uuid.uuid4().hex[:6]}.jpg"
        local_path = os.path.join(in_dir, filename)
        await tg_file.download_to_drive(local_path)
        write_attachment_meta(local_path, extra={
            "agent_id": self.AGENT_ID,
            "chat_id": chat_id,
            "kind": "photo",
            "caption": caption[:500],
            "created_at": _dt.datetime.now().isoformat(),
        })
        from dashboard.private_inbound import observe_into_vision
        observe_into_vision(local_path, agent_id=self.AGENT_ID)

        save_message(chat_id, self.AGENT_ID, "user", f"[Photo: {filename}] {caption}")
        self.journal("photo_received", f"Photo: {filename}", chat_id=chat_id)

        # Decide whether the user actually wants analysis right now.
        # Default is QUIET SAVE — Serge often pastes batches of reference
        # photos and doesn't want a room-by-room readout for each one.
        wants_analysis = any(t in caption_low for t in ANALYZE_TRIGGER_KEYWORDS)
        is_ref_upload   = self._is_reference_upload(caption)
        has_open_refs   = self._has_references(chat_id)

        # ── Reference-upload branch (avatar / character mood board) ───────────
        # An open ref session (`has_open_refs`) means Serge is mid-batch — keep
        # accepting new photos as references even when the caption omits a
        # `ref:` trigger.
        if is_ref_upload or has_open_refs:
            role = self._detect_ref_role(caption) if is_ref_upload else "general"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📥 Reference received (role: {role}, private). Analyzing facial features and style cues..."
            )
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self.skills.run, "analyze_image",
                {"image_path": local_path, "prompt": PORTRAIT_ANALYSIS_PROMPT, "mode": "analyze"},
                chat_id
            )

            ref_description = ""
            if result.get("success"):
                output = result.get("output", "")
                try:
                    parsed = json.loads(output.split("\n")[-1])
                    ref_description = parsed.get("analysis", output)
                except Exception:
                    ref_description = output.split("\n---\n")[-1] if "\n---\n" in output else output
                    if '{"success"' in ref_description:
                        ref_description = ref_description[:ref_description.index('{"success"')].strip()

            self._add_reference(chat_id, local_path, role, ref_description.strip(), caption)
            refs = self._pending_edits[chat_id].get("references", [])
            roles_summary = ", ".join(r["role"] for r in refs)

            response = (
                f"✅ Reference saved as **{role}** ({len(refs)} ref(s) total: {roles_summary})\n\n"
                f"📋 What I see:\n{ref_description[:600]}\n\n"
                f"➕ Send more references with captions like:\n"
                f"  • `ref: wardrobe` (clothing/style)\n"
                f"  • `ref: pose` (body language)\n"
                f"  • `ref: mood` (overall vibe)\n\n"
                f"🎨 When ready, say: **\"generate the cole ray avatar from refs, oil on canvas\"**"
            )
            save_message(chat_id, self.AGENT_ID, "assistant", response)
            await self._send_response(context.bot, chat_id, response)
            return

        # ── Quiet-save default ────────────────────────────────────────────────
        # No `ref:` trigger and no analyze keyword → Serge is just pasting a
        # photo, possibly mid-thought. Stash it as a private inbound, ack
        # briefly, and STOP. No room-architecture readout, no GPU spend.
        # Serge can follow up with "analyze this" / "ref: face" / "edit it"
        # to take action.
        img_hash = self._get_image_hash(local_path)
        existing_ctx = self._get_context(img_hash)

        if not wants_analysis and not existing_ctx.get("description"):
            # Track as the most recently received photo so a follow-up like
            # "analyze it" or "edit the cabinets white" can pick it up.
            slot = self._pending_edits.setdefault(chat_id, {})
            slot.update({
                "source": local_path,
                "image_hash": img_hash,
                "context_file": os.path.join(CONTEXT_DIR, f"{img_hash}.json"),
                "awaiting_instructions": True,
            })
            self._save_pending_edits()
            self.remember("last_received_photo", local_path, "images")
            # Accumulate into the per-chat burst; ONE summary message will be
            # sent _BURST_WINDOW seconds after the last photo lands.
            await self._enqueue_photo_burst(context, chat_id, filename, local_path, caption)
            return

        if existing_ctx.get("description"):
            # SKIP analysis — we've seen this image before
            description = existing_ctx["description"]
            edit_count = len(existing_ctx.get("edits", []))
            response = (
                f"📸 I recognize this image ({edit_count} previous edit(s)).\n\n"
                f"📋 Previous description:\n{description[:800]}\n\n"
                f"✏️ Tell me what changes you want — I'll generate 2 variants."
            )
            self._pending_edits[chat_id] = {
                "source": local_path,
                "description": description,
                "image_hash": img_hash,
                "context_file": os.path.join(CONTEXT_DIR, f"{img_hash}.json"),
            }
            self._save_pending_edits()
        else:
            # Full analysis
            await context.bot.send_message(chat_id=chat_id, text="🔍 Analyzing image... identifying objects, materials, geometry, viewpoint\n⏱ ~30-60 seconds")
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

            loop = asyncio.get_event_loop()
            analysis_prompt = (
                "Analyze this image in extreme detail for architectural/construction visualization. "
                "Identify and describe:\n"
                "1. VIEWPOINT: Camera angle, perspective, room orientation\n"
                "2. OBJECTS: Every visible item (cabinets, counters, appliances, fixtures, furniture)\n"
                "3. MATERIALS: Wall finish, floor type, ceiling, trim, hardware\n"
                "4. COLORS: Exact colors/tones of walls, floor, ceiling, cabinets, counters\n"
                "5. LIGHTING: Natural/artificial, direction, quality\n"
                "6. GEOMETRY: Room shape, dimensions (estimate), layout\n"
                "7. CONDITION: New/old, needs work, quality level\n"
                "8. STYLE: Modern, traditional, farmhouse, etc.\n\n"
                "Be specific and exhaustive. This description will be used to regenerate "
                "the image with modifications."
            )

            result = await loop.run_in_executor(
                None, self.skills.run, "analyze_image",
                {"image_path": local_path, "prompt": analysis_prompt, "mode": "analyze"}, chat_id
            )

            if result.get("success"):
                output = result.get("output", "")
                # Extract analysis text
                try:
                    parsed = json.loads(output.split("\n")[-1])
                    description = parsed.get("analysis", output)
                except Exception:
                    description = output.split("\n---\n")[-1] if "\n---\n" in output else output
                    if '{"success"' in description:
                        description = description[:description.index('{"success"')].strip()

                # Save context
                ctx = {
                    "description": description,
                    "source_image": local_path,
                    "image_hash": img_hash,
                    "analyzed_at": _dt.datetime.now().isoformat(),
                    "edits": [],
                }
                self._save_context(img_hash, ctx)

                response = (
                    f"📸 Image Analysis Complete\n\n"
                    f"📋 Description:\n{description[:1200]}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✏️ Tell me what to change — I'll generate 2 variants.\n"
                    f"Examples:\n"
                    f"  • add white shaker cabinets and granite countertops\n"
                    f"  • paint the walls warm gray\n"
                    f"  • install natural wood floors\n"
                    f"  • add a kitchen island with seating"
                )
                slot = self._pending_edits.setdefault(chat_id, {})
                slot.update({
                    "source": local_path,
                    "description": description,
                    "image_hash": img_hash,
                    "context_file": os.path.join(CONTEXT_DIR, f"{img_hash}.json"),
                })
                self._save_pending_edits()
            else:
                error = result.get("error", result.get("output", "Unknown error"))
                response = f"Could not analyze image: {error}\n\nDescribe what you want changed and I'll try anyway."
                slot = self._pending_edits.setdefault(chat_id, {})
                slot.update({
                    "source": local_path,
                    "description": "",
                    "image_hash": img_hash,
                })
                self._save_pending_edits()

        self.remember("last_image_analysis", description[:500] if 'description' in dir() else "", "images")
        self.remember("last_analyzed_photo", local_path, "images")
        save_message(chat_id, self.AGENT_ID, "assistant", response)
        await self._send_response(context.bot, chat_id, response)

    # ── Text Handler ──────────────────────────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text or ""
        if not text.strip():
            return

        logger.info(f"[{self.AGENT_ID}] Message from {chat_id}: {text[:80]}")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        save_message(chat_id, self.AGENT_ID, "user", text)
        self.journal("message_received", f"User: {text[:200]}", chat_id=chat_id)

        # ── Variant selection (user picks "1" or "2") ─────────────────────────
        if self._is_variant_selection(text, chat_id):
            choice = self._get_variant_choice(text)
            pending = self._pending_edits.get(chat_id, {})
            ctx = self._get_context(pending.get("image_hash", ""))
            variants = ctx.get("last_variants", [])
            if variants and choice <= len(variants):
                chosen = variants[choice - 1]
                self._record_generated(chat_id, chosen)
                # Update pending to use chosen as new source
                self._pending_edits[chat_id]["source"] = chosen
                self._save_pending_edits()
                response = (
                    f"✅ Selected variant {choice}.\n"
                    f"📁 {chosen}\n\n"
                    f"✏️ Send more edits to refine, or send a new photo."
                )
            else:
                response = "No variants to choose from. Send a photo first."
            save_message(chat_id, self.AGENT_ID, "assistant", response)
            await self._send_response(context.bot, chat_id, response)
            return

        # ── Avatar-from-references (multi-photo character generation) ─────────
        if self._is_avatar_from_refs_request(text, chat_id):
            all_refs = self._pending_edits[chat_id].get("references", [])
            # If user named a character ("generate elie"), filter to only that
            # character's refs so we don't cross-pollinate identities.
            named_refs = self._matched_refs_for_text(text, chat_id)
            refs = named_refs if named_refs else all_refs
            face_ref = next((r for r in refs if r["role"] == "face"), refs[0])
            other_refs = [r for r in refs if r is not face_ref]

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🎨 Painting from {len(refs)} reference(s)\n"
                    f"🧑 Face anchor: {Path(face_ref['path']).name}\n"
                    f"🔒 Canny ControlNet locking facial structure\n"
                    f"⏱ Sending each variant as it completes"
                )
            )
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

            # Detect gender from face ref so we never drift (canny edges of a
            # bearded face + photo-realism bias has been rendering women).
            face_desc = face_ref["description"] or ""
            face_lc = face_desc.lower()
            masculine_hits = sum(1 for kw in (
                "male", "man", "masculine", "beard", "stubble", "mustache", "moustache",
                "his ", "he ", "guy", "gentleman", "boyish",
            ) if kw in face_lc)
            feminine_hits = sum(1 for kw in (
                "female", "woman", "feminine", "her ", "she ", "girl", "lady",
            ) if kw in face_lc)
            subject = "a man" if masculine_hits >= feminine_hits else "a woman"

            # Read the user's style and composition cues from the request
            t_lc = text.lower()
            if any(k in t_lc for k in ("photo", "photograph", "photoreal", "realistic", "cinematic", "dslr", "headshot photo")):
                style_lead = f"professional studio portrait photograph of {subject}, photorealistic, sharp focus, 50mm lens"
                style_flag = "photo"
            elif any(k in t_lc for k in ("watercolor", "watercolour")):
                style_lead = f"watercolor painting of {subject}, soft washes, paper texture, fine art"
                style_flag = "oil_painting"
            elif any(k in t_lc for k in ("anime", "manga", "cartoon", "illustration", "illustrated")):
                style_lead = f"high-quality digital illustration of {subject}, clean linework, vibrant colors"
                style_flag = "illustration"
            else:
                style_lead = f"oil on canvas portrait of {subject}, classical fine art painting, John Singer Sargent style"
                style_flag = "oil_painting"

            if any(k in t_lc for k in ("full body", "full-body", "standing", "head to toe", "whole body", "wide shot")):
                composition = "full body composition, head to toe, standing pose, full figure visible"
                width_h, height_h = 640, 1024  # portrait aspect for full-body
            elif any(k in t_lc for k in ("medium shot", "half body", "waist up")):
                composition = "medium shot, waist-up framing, three-quarter view"
                width_h, height_h = 768, 1024
            else:
                composition = "head and shoulders composition, three-quarter view, looking at viewer"
                width_h, height_h = 768, 768

            wardrobe_descs = [r["description"] for r in refs if r["role"] in ("wardrobe", "general") and r["description"]]
            style_descs    = [r["description"] for r in refs if r["role"] in ("style", "lighting", "setting") and r["description"]]

            sd_prompt_parts = [
                style_lead,
                face_desc[:600] if face_desc else "",
            ]
            for w in wardrobe_descs[:1]:
                sd_prompt_parts.append("wardrobe details: " + w[:300])
            for s in style_descs[:1]:
                sd_prompt_parts.append("style/setting: " + s[:300])
            sd_prompt_parts.append(
                composition + ", clearly visible facial features, "
                "moody dramatic lighting, museum quality"
            )
            sd_prompt = ", ".join(p for p in sd_prompt_parts if p)

            paths_collected: list[str] = []
            for vi in range(3):
                result = self.skills.run("generate_image", {
                    "prompt": sd_prompt,
                    "style": style_flag,
                    "subject_gender": "male" if subject == "a man" else "female",
                    "reference_image": face_ref["path"],
                    "controlnet_mode": "canny",
                    "controlnet_weight": 0.7,
                    "width": width_h, "height": height_h,
                    "adetailer": False,
                    "n_iter": 1,
                }, chat_id=chat_id)
                if result.get("success"):
                    out = result.get("output", "")
                    found = re.findall(r'(/[^\s"]+\.(?:png|jpg|jpeg|webp))', out)
                    for p in found[:1]:
                        if Path(p).exists() and p not in paths_collected:
                            paths_collected.append(p)
                            self._record_generated(chat_id, p)
                            try:
                                with open(p, "rb") as f:
                                    await context.bot.send_photo(
                                        chat_id=chat_id,
                                        photo=InputFile(f, filename=Path(p).name),
                                        caption=f"🎨 variant {vi+1}/3" + (f"\n✏️ {text[:80]}" if vi == 0 else "")
                                    )
                            except Exception as e:
                                logger.error(f"Failed to send variant {vi+1}: {e}")
                else:
                    logger.error(f"Avatar variant {vi+1} failed: {result.get('error', '')[:200]}")

            if paths_collected:
                ctx_key = self._pending_edits[chat_id].get("image_hash") or face_ref["path"]
                self._pending_edits[chat_id]["last_variants"] = paths_collected
                self._save_pending_edits()
                response = (
                    f"✏️ {text[:100]}\n\n"
                    f"Reply **1**, **2**, or **3** to lock that face as canonical, "
                    f"or send more refs / edits."
                )
            else:
                response = "Avatar generation failed. Check Sam logs: journalctl -u baza-agent-sam-axe -n 50"
            save_message(chat_id, self.AGENT_ID, "assistant", response)
            await self._send_response(context.bot, chat_id, response)
            return

        # ── Edit request (user wants changes to pending image) ────────────────
        if self._is_edit_request(text, chat_id):
            pending = self._pending_edits[chat_id]
            source = pending.get("source", "")
            description = pending.get("description", "")
            img_hash = pending.get("image_hash", "")

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🎨 Rendering: {text[:80]}...\n"
                    f"🔒 Dual ControlNet (depth + edges) preserving source structure\n"
                    f"⏱ Sending each variant as it completes"
                )
            )
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self.skills.run, "image_edit",
                {
                    "source_image": source,
                    "description": description,
                    "edit_request": text,
                    "variants": 3,
                }, chat_id
            )

            if result.get("success"):
                output = result.get("output", "")
                paths = re.findall(r'(/[^\s"]+\.(?:png|jpg|jpeg|webp))', output)
                if paths:
                    ctx = self._get_context(img_hash)
                    ctx["last_variants"] = paths[:3]
                    self._save_context(img_hash, ctx)

                    # Send each variant individually for faster delivery
                    labels = ["1st", "2nd", "3rd"]
                    for i, p in enumerate(paths[:3]):
                        if Path(p).exists():
                            self._record_generated(chat_id, p)
                            try:
                                with open(p, "rb") as f:
                                    await context.bot.send_photo(
                                        chat_id=chat_id,
                                        photo=InputFile(f, filename=Path(p).name),
                                        caption=f"🎨 {labels[i] if i < len(labels) else f'#{i+1}'} try"
                                            + (f"\n✏��� {text[:80]}" if i == 0 else "")
                                    )
                            except Exception as e:
                                logger.error(f"Failed to send variant {i+1}: {e}")

                    response = (
                        f"✏️ {text[:100]}\n\n"
                        f"Reply **1**, **2**, or **3** to select, or send more edits."
                    )
                else:
                    response = f"Generation completed but no image files found.\n{output[:300]}"
            else:
                error = result.get("error", result.get("output", "Unknown error"))
                response = f"Image edit failed: {error[:200]}"

            save_message(chat_id, self.AGENT_ID, "assistant", response)
            await self._send_response(context.bot, chat_id, response)
            return

        # ── Show request ──────────────────────────────────────────────────────
        if self._is_show_request(text):
            paths = self._session_images.get(chat_id, [])
            existing = [p for p in paths if Path(p).exists()]
            if existing:
                await self._send_variants(chat_id, existing[-2:], context)
                response = f"📤 {min(len(existing),2)} image(s) sent."
            else:
                response = "No images in session. Send a photo or ask me to generate one."
            save_message(chat_id, self.AGENT_ID, "assistant", response)
            await self._send_response(context.bot, chat_id, response)
            return

        # ── Print request ─────────────────────────────────────────────────────
        if self._is_print_request(text):
            await context.bot.send_message(chat_id=chat_id, text="Sending to printer...")
            reply = await self._handle_print_request(text, chat_id)
            save_message(chat_id, self.AGENT_ID, "assistant", reply)
            await self._send_response(context.bot, chat_id, reply)
            return

        # ── Image generation from scratch ─────────────────────────────────────
        if self._is_image_request(text):
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎨 Generating 2 variants... ~1-2 min"
            )
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

            history = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
            messages = [{"role": h["role"], "content": h["content"]} for h in history]
            system = self.build_system_prompt()
            loop = asyncio.get_event_loop()

            # Have the LLM craft a detailed SD prompt
            prompt_msgs = messages + [{
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "[TASK: Write a detailed Stable Diffusion prompt for this request. "
                    "Include: subject, style, materials, lighting, camera angle, quality tags. "
                    "Output ONLY the prompt text, nothing else. No explanations.]"
                )
            }]
            sd_prompt = await loop.run_in_executor(
                None, self.llm_chat, prompt_msgs, system
            )
            sd_prompt = (sd_prompt or "").strip().strip('"').strip("'")
            # qwen3-vl periodically returns blank or one-token output for
            # text-only drafting requests. Fall back to the user's text plus
            # quality tags so we never ship an empty prompt to SD.
            if len(sd_prompt) < 20:
                logger.warning(f"[sam_axe] LLM drafted thin prompt ({len(sd_prompt)} chars), using fallback")
                sd_prompt = (
                    f"{text}, masterpiece, best quality, highly detailed, "
                    f"sharp focus, professional, 8k, intricate, cinematic lighting"
                )

            result = self.skills.run("generate_image", {
                "prompt": sd_prompt,
                "width": 768, "height": 768,
                "n_iter": 3,
            }, chat_id=chat_id)

            if result.get("success"):
                output = result.get("output", "")
                paths = re.findall(r'(/[^\s"]+\.(?:png|jpg|jpeg|webp))', output)
                if paths:
                    await self._send_variants(chat_id, paths[:3], context, text)
                    for p in paths[:3]:
                        self._record_generated(chat_id, p)
                    response = f"🎨 Generated from: {text[:100]}\n\nReply 1, 2, or 3 to select."
                else:
                    response = f"Generated but no files found.\n{output[:300]}"
            else:
                response = f"Generation failed: {result.get('error', 'unknown')[:200]}"

            save_message(chat_id, self.AGENT_ID, "assistant", response)
            self.journal("llm_response", f"Generated image: {text[:100]}", result=response[:300], success=True, chat_id=chat_id)
            await self._send_response(context.bot, chat_id, response)
            return

        # ── Normal text conversation ──────────────────────────────────────────
        history = get_history(chat_id, self.AGENT_ID, limit=MAX_HISTORY)
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        system = self.build_system_prompt()
        loop = asyncio.get_event_loop()

        messages_with_user = messages + [{"role": "user", "content": text + "\n\n[FORMATTING: No markdown headers. Use emoji. Plain text. Short.]"}]
        response = await loop.run_in_executor(
            None, self.llm_chat, messages_with_user, system
        )

        if not response:
            response = "_(no response)_"

        # Execute any skill calls
        response, skill_results = self.skills.parse_and_run(response, chat_id=chat_id)

        # Send any generated images from skill calls
        for r in skill_results:
            if r.get("success"):
                out = r.get("output", "")
                paths = re.findall(r'(/[^\s"]+\.(?:png|jpg|jpeg|webp))', out)
                for p in paths:
                    if Path(p).exists():
                        self._record_generated(chat_id, p)
                        try:
                            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                            with open(p, "rb") as f:
                                await context.bot.send_photo(
                                    chat_id=chat_id,
                                    photo=InputFile(f, filename=Path(p).name),
                                    caption=f"🎨 {Path(p).stem[:80]}"
                                )
                        except Exception as e:
                            logger.error(f"Failed to send: {e}")

        save_message(chat_id, self.AGENT_ID, "assistant", response)
        self.journal("llm_response", f"Responded to: {text[:100]}", result=response[:300], success=True, chat_id=chat_id)
        self._auto_remember(chat_id, text, response)
        await self._send_response(context.bot, chat_id, response)

    # ── Override run() to add photo handler ────────────────────────────────────

    async def run(self):
        token = os.environ.get(self.TOKEN_ENV)
        if not token:
            raise ValueError(f"[{self.AGENT_ID}] Missing token: {self.TOKEN_ENV}")

        app = Application.builder().token(token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        # Any non-photo attachment (PDFs, videos, audio, voice, generic docs)
        # gets stashed in Data Hub and rolled into the same burst-summary.
        app.add_handler(MessageHandler(
            filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE,
            self.handle_attachment_quiet,
        ))

        logger.info(f"[{self.AGENT_ID}] Starting Telegram bot (with photo + edit pipeline + attachment intake)...")

        async with app:
            await app.initialize()
            await app.start()
            asyncio.ensure_future(self._heartbeat_loop())
            asyncio.ensure_future(self._artifact_context_loop())
            await app.updater.start_polling(drop_pending_updates=True)
            try:
                await asyncio.Event().wait()
            finally:
                await app.updater.stop()
                await app.stop()

    def _auto_remember(self, chat_id: int, user_msg: str, agent_reply: str):
        super()._auto_remember(chat_id, user_msg, agent_reply)
        style_match = re.search(
            r'(?:style|look|aesthetic)[:\s]+([^\.\,\n]+)',
            user_msg, re.IGNORECASE
        )
        if style_match:
            self.remember("last_style_requested", style_match.group(1).strip()[:100], "style")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    agent = SamAxe()
    asyncio.run(agent.run())
