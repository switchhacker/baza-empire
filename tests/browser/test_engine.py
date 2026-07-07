import asyncio

import pytest

from browser.engine import Engine


@pytest.mark.integration
def test_render_data_url():
    async def go():
        eng = Engine()
        await eng.start()
        try:
            out = await eng.render(
                "data:text/html,<html><head><title>T1</title></head>"
                "<body><h1>hello-engine</h1></body></html>"
            )
            return out
        finally:
            await eng.stop()

    out = asyncio.run(go())
    assert "hello-engine" in out["html"]
    assert out["screenshot_path"] is None


def test_polite_wait_spaces_same_domain():
    async def go():
        eng = Engine(domain_delay=0.2)
        import time
        t0 = time.monotonic()
        await eng._polite_wait("https://same.com/a")
        await eng._polite_wait("https://same.com/b")
        return time.monotonic() - t0

    assert asyncio.run(go()) >= 0.2


def test_polite_wait_concurrent_same_host_serializes():
    """Three concurrent _polite_wait() calls to the same host must not all
    read the same stale _last_hit and sleep in parallel — the per-host lock
    should force them through the check/sleep/update sequence one at a time,
    giving two enforced domain_delay gaps (call 1 is free, call 2 waits behind
    call 1, call 3 waits behind call 2). Without the lock this would race and
    finish in ~1 delay instead of ~2."""
    import time

    async def go():
        eng = Engine(domain_delay=0.2)
        t0 = time.monotonic()
        await asyncio.gather(
            eng._polite_wait("http://x.test/a"),
            eng._polite_wait("http://x.test/b"),
            eng._polite_wait("http://x.test/c"),
        )
        return time.monotonic() - t0

    assert asyncio.run(go()) >= 0.4


def test_polite_wait_different_hosts_do_not_serialize():
    """Per-host locks must not become a single global lock: concurrent waits
    for two different hosts should overlap, not stack."""
    import time

    async def go():
        eng = Engine(domain_delay=0.2)
        # Prime each host once so the first (always-free) hit doesn't mask
        # whether the second round of waits overlaps or serializes.
        await eng._polite_wait("http://host1.test/x")
        await eng._polite_wait("http://host2.test/x")
        t0 = time.monotonic()
        await asyncio.gather(
            eng._polite_wait("http://host1.test/y"),
            eng._polite_wait("http://host2.test/y"),
        )
        return time.monotonic() - t0

    elapsed = asyncio.run(go())
    # Each host individually waits ~domain_delay (0.2s). If hosts serialized
    # against each other (bug: one lock for all hosts) this would take ~0.4s;
    # in parallel it should land close to a single 0.2s wait. Use a threshold
    # comfortably between the two (well under the 0.4s serialized case) to
    # stay robust against scheduling jitter.
    assert elapsed < 0.35
