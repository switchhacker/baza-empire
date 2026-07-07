"""Playwright lifecycle: one shared headless Chromium, contexts capped by a
semaphore, per-domain politeness delay, auto-relaunch if Chromium dies."""
import asyncio
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Error as PWError

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_FRAMEWORK_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = _FRAMEWORK_DIR / "dashboard" / "artifacts" / "browser"


class Engine:
    def __init__(self, max_contexts: int = 4, domain_delay: float = 1.0):
        self._pw = None
        self._browser = None
        self._sem = asyncio.Semaphore(max_contexts)
        self._domain_delay = domain_delay
        self._last_hit: dict[str, float] = {}
        self._relaunch_lock = asyncio.Lock()
        self._host_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)

    async def stop(self) -> None:
        """Close the shared browser and Playwright instance.

        Does not close caller-owned contexts obtained via new_context() —
        those were never tracked by Engine and remain the caller's
        responsibility to close.
        """
        for closer in (self._browser, self._pw):
            try:
                if closer is self._browser and self._browser:
                    await self._browser.close()
                elif closer is self._pw and self._pw:
                    await self._pw.stop()
            except Exception:
                pass
        self._browser = None
        self._pw = None

    async def _ensure_browser(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        async with self._relaunch_lock:
            if self._browser is None or not self._browser.is_connected():
                await self.stop()
                await self.start()

    async def _polite_wait(self, url: str) -> None:
        host = urlparse(url).netloc
        if not host:
            return
        # Serialize check/sleep/update per host so concurrent render() calls
        # to the same host can't both read a stale _last_hit and sleep the
        # same amount in parallel (that would only enforce the delay between
        # sequential calls, not concurrent ones). The lock is held only for
        # the duration of this method — never across the page fetch itself —
        # so it cannot deadlock with the render() semaphore or anything else.
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last_hit.get(host, 0.0)
            wait = self._domain_delay - (time.monotonic() - last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_hit[host] = time.monotonic()

    async def new_context(self):
        """Return a caller-owned, anonymous browser context.

        This context is NOT tracked by Engine and is NOT counted against the
        render() semaphore (max_contexts) — that cap only bounds render()'s
        own short-lived page-fetch contexts. Long-lived interactive sessions
        built on new_context() (e.g. a SessionManager with its own
        max_sessions cap) are capped separately by their own manager;
        gating this method on the render semaphore would deadlock, since a
        pool of long-lived sessions can outnumber the semaphore's slots and
        hold contexts for minutes at a time.

        The caller owns the returned context and is responsible for closing
        it when done — Engine.stop() does not close contexts created here.
        """
        await self._ensure_browser()
        return await self._browser.new_context(user_agent=UA)

    async def render(self, url: str, wait_ms: int = 0, screenshot: bool = False) -> dict:
        await self._ensure_browser()
        async with self._sem:
            await self._polite_wait(url)
            ctx = await self._browser.new_context(user_agent=UA)
            try:
                page = await ctx.new_page()
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except PWError:
                    pass  # busy pages never go idle; domcontentloaded is enough
                if wait_ms:
                    await page.wait_for_timeout(min(int(wait_ms), 10000))
                shot = None
                if screenshot:
                    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                    shot = str(SCREENSHOT_DIR / f"scrape_{int(time.time() * 1000)}.png")
                    await page.screenshot(path=shot, full_page=False)
                return {
                    "html": await page.content(),
                    "final_url": page.url,
                    "status": resp.status if resp else None,
                    "screenshot_path": shot,
                }
            finally:
                await ctx.close()
