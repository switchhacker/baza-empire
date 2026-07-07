"""Stateful interactive browser sessions. The agent reads the page as markdown
plus a NUMBERED list of interactive elements (tagged in-DOM with data-pb-idx),
then acts by index. Sessions idle out after idle_ttl seconds."""
import asyncio
import time
import uuid
from pathlib import Path

from playwright.async_api import Error as PWError

try:
    from browser.engine import SCREENSHOT_DIR
    from browser.page_to_md import page_to_md
    from browser import db
except ImportError:  # pragma: no cover
    from engine import SCREENSHOT_DIR
    from page_to_md import page_to_md
    import db

READ_JS = """() => {
  document.querySelectorAll('[data-pb-idx]').forEach(el => el.removeAttribute('data-pb-idx'));
  const els = Array.from(document.querySelectorAll(
    'a[href], button, input, select, textarea, [role="button"], [onclick]'
  )).filter(el => {
    const st = window.getComputedStyle(el);
    return st.display !== 'none' && st.visibility !== 'hidden';
  }).slice(0, 150);
  return els.map((el, i) => {
    el.setAttribute('data-pb-idx', String(i));
    const f = el.closest('form');
    const label = (el.innerText || el.value || el.placeholder ||
                   el.getAttribute('aria-label') || el.getAttribute('title') || ''
                  ).trim().replace(/\\s+/g, ' ').slice(0, 80);
    return {idx: i, tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            text: label, in_form: !!f,
            form_method: f ? (f.method || 'get').toLowerCase() : ''};
  });
}"""

ELEMENT_INFO_JS = """(idx) => {
  const el = document.querySelector('[data-pb-idx="' + idx + '"]');
  if (!el) return null;
  const f = el.closest('form');
  return {tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
          text: (el.innerText || el.value || '').trim().slice(0, 80),
          in_form: !!f, form_method: f ? (f.method || 'get').toLowerCase() : '',
          href: el.tagName.toLowerCase() === 'a' ? (el.getAttribute('href') || '') : ''};
}"""

ACTIVE_ELEMENT_JS = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const f = el.closest('form');
  return {tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '',
          text: (el.innerText || el.value || '').trim().slice(0, 80),
          in_form: !!f, form_method: f ? (f.method || 'get').toLowerCase() : ''};
}"""


class Session:
    def __init__(self, sid: str, context, page, profile: str | None):
        self.id = sid
        self.context = context
        self.page = page
        self.profile = profile
        self.last_used = time.monotonic()
        # Set while a write-gate approval is pending on this session (finding
        # 2a, whole-branch review): freezes further mutating acts so the
        # agent can't re-read/renavigate the page and drift the element the
        # pending approval was described against while Serge is deciding.
        self.pending_approval_id: int | None = None

    def touch(self):
        self.last_used = time.monotonic()


# Ops that change page/navigation state. Frozen while a session has a pending
# approval; read/screenshot/element_info are informational and stay allowed.
# "back" is included (finding 2, round 2 review): it's a navigation op like
# goto/click and must not be a bypass an agent can use between a gated
# request and Serge's decision.
MUTATING_OPS = {"goto", "click", "type", "press", "scroll", "back"}


class SessionManager:
    def __init__(self, engine, idle_ttl: int = 600, max_sessions: int = 8):
        self.engine = engine
        self.idle_ttl = idle_ttl
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}
        self._create_lock = asyncio.Lock()

    async def create(self, profile: str | None = None) -> str:
        async with self._create_lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(f"max {self.max_sessions} sessions; close one first")
            ctx = await self.engine.new_context(profile=profile)
            page = ctx.pages[0] if getattr(ctx, "pages", None) else await ctx.new_page()
            sid = uuid.uuid4().hex[:12]
            self._sessions[sid] = Session(sid, ctx, page, profile)
            return sid

    def get(self, sid: str) -> Session:
        s = self._sessions.get(sid)
        if s is None:
            raise KeyError(sid)
        s.touch()
        return s

    async def close(self, sid: str) -> None:
        s = self._sessions.pop(sid, None)
        if s:
            try:
                await s.context.close()
            except Exception:
                pass

    async def close_all(self) -> None:
        for sid in list(self._sessions):
            await self.close(sid)

    async def reap_once(self) -> int:
        now = time.monotonic()
        stale = [sid for sid, s in self._sessions.items()
                 if now - s.last_used > self.idle_ttl]
        for sid in stale:
            await self.close(sid)
        return len(stale)

    def mark_pending_approval(self, sid: str, approval_id: int) -> None:
        """Freeze sid: further mutating acts refuse until the approval is
        decided (approve/deny/expire) and clear_pending_approval is called."""
        self.get(sid).pending_approval_id = approval_id

    def clear_pending_approval(self, sid: str, approval_id: int | None = None) -> None:
        """Clear sid's freeze. If approval_id is given, only clear when it
        matches the session's current marker — so deciding one approval can't
        unfreeze a session whose live marker belongs to a still-pending second
        approval (concurrent-gated-request race). Pass None to force-clear."""
        try:
            s = self.get(sid)
        except KeyError:
            return
        if approval_id is not None and s.pending_approval_id != approval_id:
            return
        s.pending_approval_id = None

    def pending_block(self, sid: str) -> dict | None:
        """Structured refusal if sid has an unresolved approval, else None.

        DB-authoritative and self-healing (finding 1, round 2 review): the
        freeze only holds while the backing approval row is genuinely still
        'pending' in the database. The 60s reaper's db.expire_stale(300)
        sweep (the documented "silence = denied" default) flips a stale
        approval to 'expired' directly in the DB — it has no idea which
        session that approval belongs to, so it can't clear the session's
        marker itself. If left unchecked, that marker would freeze the
        session forever after any 5-minute silence, even though the
        approval it was guarding is long since resolved.
        So every time this is consulted, re-check the approval's live
        status: if it's gone (unknown id) or no longer 'pending' — expired,
        denied, approved, executed, error, anything terminal, decided by
        ANY path (reaper sweep, a direct /approvals/{id}/decide hit, etc.) —
        clear the stale marker here and let the caller through instead of
        blocking on a decision that has already been made.
        Raises KeyError for an unknown session id (callers already catch
        that the same way they do for act())."""
        s = self.get(sid)
        pid = s.pending_approval_id
        if pid is None:
            return None
        approval = db.get_approval(pid)
        if approval is None or approval.get("status") != "pending":
            self.clear_pending_approval(sid)
            return None
        return {"success": False,
                "error": "approval pending; resolve it before acting",
                "approval_id": pid}

    async def element_info(self, sid: str, index: int):
        s = self.get(sid)
        return await s.page.evaluate(ELEMENT_INFO_JS, int(index))

    async def active_element(self, sid: str):
        s = self.get(sid)
        return await s.page.evaluate(ACTIVE_ELEMENT_JS)

    async def read(self, sid: str, max_chars: int = 6000) -> dict:
        s = self.get(sid)
        elements = await s.page.evaluate(READ_JS)
        html = await s.page.content()
        md = page_to_md(html, s.page.url, max_chars=max_chars)
        return {"success": True, "url": s.page.url, "title": md["title"],
                "markdown": md["markdown"], "elements": elements}

    async def act(self, sid: str, op: str, **kw) -> dict:
        s = self.get(sid)
        if op in MUTATING_OPS:
            # Routed through pending_block so the DB-authoritative self-heal
            # (finding 1, round 2 review) applies uniformly here too, not
            # just to the routes in server.py that call pending_block()
            # directly before the gating decision.
            blocked = self.pending_block(sid)
            if blocked:
                return blocked
        page = s.page
        try:
            if op == "goto":
                await page.goto(kw["url"], wait_until="domcontentloaded", timeout=30000)
            elif op == "click":
                if kw.get("index") is None:
                    return {"success": False,
                            "error": "missing required field: index",
                            "hint": "call read to get element indexes"}
                loc = page.locator(f'[data-pb-idx="{int(kw["index"])}"]')
                if await loc.count() == 0:
                    return {"success": False,
                            "error": f'no element with index {kw["index"]}',
                            "hint": "call read to refresh element indexes"}
                await loc.click(timeout=10000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                except PWError:
                    pass
            elif op == "type":
                if kw.get("index") is None:
                    return {"success": False,
                            "error": "missing required field: index",
                            "hint": "call read to get element indexes"}
                loc = page.locator(f'[data-pb-idx="{int(kw["index"])}"]')
                if await loc.count() == 0:
                    return {"success": False,
                            "error": f'no element with index {kw["index"]}',
                            "hint": "call read to refresh element indexes"}
                await loc.fill(kw.get("text", ""), timeout=10000)
                await loc.focus()
            elif op == "press":
                await page.keyboard.press(kw.get("key", "Enter"))
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=8000)
                except PWError:
                    pass
            elif op == "scroll":
                await page.mouse.wheel(0, int(kw.get("dy", 800)))
            elif op == "back":
                await page.go_back(wait_until="domcontentloaded", timeout=15000)
            elif op == "screenshot":
                SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                path = str(SCREENSHOT_DIR / f"session_{sid}_{int(time.time()*1000)}.png")
                await page.screenshot(path=path, full_page=False)
                return {"success": True, "url": page.url, "screenshot_path": path}
            else:
                return {"success": False, "error": f"unknown op '{op}'",
                        "hint": "ops: goto/click/type/press/scroll/back/screenshot"}
            return {"success": True, "url": page.url}
        except (PWError, ValueError, TypeError) as e:
            return {"success": False, "error": f"playwright: {str(e).splitlines()[0]}",
                    "hint": "call read to see current page state"}
