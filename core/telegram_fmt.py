"""Markdown → Telegram HTML formatting + safe senders for all outbound paths.

LLM agents write markdown; Telegram renders HTML. This module converts
between them, chunks at the 4096 limit without splitting tags, and falls
back to plain text on Telegram parse errors so a message is never lost.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

MAX_LEN = 4000

_HTML_TAG_RE = re.compile(r"</?(b|i|u|s|code|pre|a|blockquote|tg-spoiler)\b", re.I)
_MD_MARK_RE = re.compile(
    r"(\*\*.+?\*\*|__.+?__|^#{1,6}\s|^\s*[-*]\s|```|\[[^\]]+\]\([^)]+\))", re.M
)
_ALLOWED_TAG_FULL_RE = re.compile(
    r"(?:"
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|pre|blockquote|tg-spoiler|span|a|code)>"
    r"|<a\s+href=\"https?://[^\"<>]*\">"
    r"|<code\s+class=\"language-[\w+-]+\">"
    r")",
    re.I,
)
_ENTITY_RE = re.compile(r"&(amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);")


def _is_safe_telegram_html(text: str) -> bool:
    """True only if text is already Telegram-ready HTML: allowed tags only,
    no bare < > and & only in entities."""
    if not _HTML_TAG_RE.search(text) or _MD_MARK_RE.search(text):
        return False
    residue = _ALLOWED_TAG_FULL_RE.sub("", text)
    if "<" in residue or ">" in residue:
        return False
    if "&" in _ENTITY_RE.sub("", residue):
        return False
    return True


def md_to_html(text: str) -> str:
    """Convert LLM-flavored markdown to Telegram-safe HTML."""
    if not isinstance(text, str):
        text = str(text)
    # Strip literal NUL bytes so they can't collide with internal stash sentinels
    text = text.replace("\x00", "")
    # Pass-through: already well-formed Telegram HTML with no markdown markers
    if _is_safe_telegram_html(text):
        return text

    stash: list[str] = []

    def _hold(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    # 1. Fenced code blocks first (no inner conversion)
    def _fence(m):
        lang = (m.group(1) or "").strip()
        code = _html.escape(m.group(2))
        if lang:
            return _hold(f'<pre><code class="language-{lang}">{code}</code></pre>')
        return _hold(f"<pre>{code}</pre>")

    text = re.sub(r"```([^\n`]*)\n(.*?)```", _fence, text, flags=re.DOTALL)

    # 2. Inline code (no inner conversion)
    text = re.sub(
        r"`([^`\n]+)`", lambda m: _hold(f"<code>{_html.escape(m.group(1))}</code>"), text
    )

    # 3. Tables → <pre> (contiguous lines that start and end with |)
    def _table(m):
        return _hold(f"<pre>{_html.escape(m.group(0).rstrip())}</pre>")

    text = re.sub(r"(?:^\|.*\|[ \t]*$\n?){2,}", _table, text, flags=re.M)

    # 4. Escape everything else
    text = _html.escape(text, quote=False)

    # 5. Links (http/https only; other schemes degrade to plain text)
    def _link(m):
        label, url = m.group(1), m.group(2)
        if re.match(r"https?://", url, re.I):
            return _hold(f'<a href="{url.replace(chr(34), "%22")}">{label}</a>')
        return label

    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", _link, text)

    # 6. Bold then italic (order matters: ** before *)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: _hold(f"<b>{m.group(1)}</b>"), text)
    text = re.sub(r"__(.+?)__", lambda m: _hold(f"<b>{m.group(1)}</b>"), text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", lambda m: _hold(f"<i>{m.group(1)}</i>"), text)
    text = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", lambda m: _hold(f"<i>{m.group(1)}</i>"), text)

    # 7. Line-level: headers, checklists, bullets, hr
    lines = []
    for line in text.split("\n"):
        h = re.match(r"^#{1,6}\s+(.*)$", line)
        if h:
            lines.append(f"<b>{h.group(1).strip()}</b>")
            continue
        line = re.sub(r"^(\s*)[-*]\s+\[x\]\s+", r"\g<1>✅ ", line, flags=re.I)
        line = re.sub(r"^(\s*)[-*]\s+\[ \]\s+", r"\g<1>☐ ", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\g<1>• ", line)
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            line = "───────"
        lines.append(line)
    text = "\n".join(lines)

    # 8. Restore stashed fragments (loop: stashed fragments may nest placeholders)
    while "\x00" in text:
        new = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
        if new == text:
            break
        text = new
    return text.strip()


def strip_markdown(text: str) -> str:
    """Remove markdown so the text reads clean as plain text (fallback path)."""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    text = re.sub(r"^- ", "", text, flags=re.MULTILINE)
    return text.strip()


def html_to_plain(html_text: str) -> str:
    """Strip HTML tags + unescape entities — used when Telegram rejects a chunk."""
    return _html.unescape(re.sub(r"</?[^>]+>", "", html_text)).strip()


def chunk_html(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on line boundaries at <= limit chars; keep <pre> balanced per chunk."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # pathological single line — hard split
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        cand = (cur + "\n" + line) if cur else line
        if len(cand) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    # Re-balance <pre> across chunk boundaries
    out, carry = [], False
    for c in chunks:
        if carry:
            c = "<pre>" + c
        open_pre = len(re.findall(r"<pre\b", c)) - c.count("</pre>")
        if open_pre > 0:
            c += "</pre>"
            carry = True
        else:
            carry = False
        out.append(c)
    return out


def _is_parse_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "parse entities" in msg or "can't parse" in msg or "unsupported start tag" in msg


async def send_html(bot, chat_id, text, already_html: bool = False, **kwargs):
    """Async sender for PTB bots: convert, chunk, send HTML, fall back to plain."""
    html_text = text if already_html else md_to_html(text)
    chunks = chunk_html(html_text)
    for i, chunk in enumerate(chunks):
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML", **kwargs)
        except Exception as e:
            if not _is_parse_error(e):
                raise
            logger.warning("telegram_fmt: HTML parse rejected, plain fallback: %.120s", chunk)
            await bot.send_message(chat_id=chat_id, text=html_to_plain(chunk), **kwargs)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.3)


def post_html(token: str, chat_id, text: str, already_html: bool = False,
              disable_web_page_preview: bool = True, timeout: int = 15) -> bool:
    """Sync sender for cron scripts / skills: convert, chunk, send, fall back."""
    if not token or not chat_id:
        logger.warning("telegram_fmt.post_html: missing token/chat_id")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    html_text = text if already_html else md_to_html(text)
    chunks = chunk_html(html_text)
    ok_all = True
    for i, chunk in enumerate(chunks):
        try:
            r = requests.post(url, json={
                "chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                "disable_web_page_preview": disable_web_page_preview,
            }, timeout=timeout)
            if not r.ok:
                logger.warning(
                    "telegram_fmt: HTML send failed (%s), plain fallback",
                    getattr(r, "status_code", "?"),
                )
                r = requests.post(url, json={
                    "chat_id": chat_id, "text": html_to_plain(chunk),
                    "disable_web_page_preview": disable_web_page_preview,
                }, timeout=timeout)
            ok_all = ok_all and r.ok
        except Exception as e:
            logger.error("telegram_fmt.post_html error: %s", e)
            ok_all = False
        if i < len(chunks) - 1:
            time.sleep(0.3)
    return ok_all
