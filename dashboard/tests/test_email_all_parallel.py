"""The "All inboxes" fetch (_threads_all) must fetch accounts AND per-thread
hydration concurrently, not sequentially.

Before: 4 accounts × (list + N×get) ran serially -> ~17s in production.
After: bounded thread pools fan the work out. Each task gets its OWN Gmail
service + DB connection (never a shared connection — that is the same
non-thread-safe-socket race that caused [SSL: WRONG_VERSION_NUMBER]).
"""
import sys
import os
import time

import pytest

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

import email_studio


_UNIT = 0.3  # simulated per-call latency


def _slow_gmail(aid=None):
    class Svc:
        def users(self):
            return self

        def threads(self):
            return self

        def list(self, **k):
            class E:
                def execute(_self):
                    time.sleep(_UNIT)  # simulate threads().list network latency
                    return {"threads": [{"id": "t-" + aid, "snippet": "s"}]}
            return E()
    return Svc()


def _slow_hydrate(svc, con, t, account_id, account_email):
    time.sleep(_UNIT)  # simulate per-thread threads().get network latency
    return {
        "thread_id": t["id"],
        "received_at": "2026-06-16T00:00:0" + t["id"][-1],
        "account_id": account_id,
        "account_email": account_email,
    }


def test_threads_all_fetches_concurrently(client, monkeypatch):
    accounts = [{"id": "a%d" % i, "email": "u%d@x.com" % i} for i in range(4)]
    monkeypatch.setattr(email_studio, "_all_accounts", lambda: accounts)
    monkeypatch.setattr(email_studio, "_gmail", _slow_gmail)
    monkeypatch.setattr(email_studio, "_hydrate_thread", _slow_hydrate)

    t0 = time.time()
    r = client.get("/api/email2/threads?account=ALL&limit=10")
    elapsed = time.time() - t0

    data = r.get_json()
    # Correctness preserved: one thread per account, merged + sorted desc.
    assert len(data["threads"]) == 4
    ids = [t["thread_id"] for t in data["threads"]]
    assert ids == sorted(ids, reverse=True)
    # Sequential would be 4×(list+hydrate) = ~2.4s. Parallel collapses to ~2×_UNIT.
    assert elapsed < 1.2, f"appears sequential: {elapsed:.2f}s for 4 accounts"


def test_threads_all_skips_gmail_build_for_cached_threads(client, monkeypatch):
    """A cache hit must not build a Gmail service (which would issue a needless
    last_used UPDATE+commit per thread). Only the per-account list call builds
    a service; cached hydration uses the DB and builds nothing."""
    import sqlite3
    accounts = [{"id": "a1", "email": "one@x.com"}, {"id": "a2", "email": "two@x.com"}]
    monkeypatch.setattr(email_studio, "_all_accounts", lambda: accounts)

    # Seed cached rows whose thread_ids match the stubs each account's list returns.
    con = email_studio._conn()
    con.execute("DELETE FROM emails")
    for aid in ("a1", "a2"):
        con.execute(
            "INSERT INTO emails (id, gmail_id, thread_id, subject, received_at, account_id) "
            "VALUES (?,?,?,?,?,?)",
            (aid, "g-" + aid, "thr-" + aid, "Hi " + aid, "2026-06-16T00:00:00", aid),
        )
    con.commit()
    con.close()

    calls = {"n": 0}

    def counting_gmail(aid=None):
        calls["n"] += 1  # counts every service build

        class Svc:
            def users(self): return self
            def threads(self): return self
            def list(self, **k):
                class E:
                    def execute(_s): return {"threads": [{"id": "thr-" + aid, "snippet": "s"}]}
                return E()
            def get(self, **k):
                raise AssertionError("threads().get must not run for a cached thread")
        return Svc()

    monkeypatch.setattr(email_studio, "_gmail", counting_gmail)

    r = client.get("/api/email2/threads?account=ALL&limit=10")
    data = r.get_json()
    assert len(data["threads"]) == 2
    assert all(t["cached"] for t in data["threads"])
    # Exactly one build per account for the list call; zero for cached hydration.
    assert calls["n"] == 2, f"built {calls['n']} services; cached hydration should build none"
