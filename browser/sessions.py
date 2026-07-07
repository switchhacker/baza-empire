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
except ImportError:  # pragma: no cover
    from engine import SCREENSHOT_DIR
    from page_to_md import page_to_md

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
          in_form: !!f, form_method: f ? (f.method || 'get').toLowerCase() : ''};
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

    def touch(self):
        self.last_used = time.monotonic()


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
