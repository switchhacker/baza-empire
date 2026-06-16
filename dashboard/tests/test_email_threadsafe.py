"""Regression: Gmail service must not share one httplib2/OpenSSL connection
across Flask worker threads.

Root cause of the "All inboxes" `[SSL: WRONG_VERSION_NUMBER]` error: `_gmail()`
cached ONE googleapiclient service (one httplib2 connection) per account, and
Flask's dev server is threaded (`app.run(threaded=True)`). Concurrent requests
(threads?account=ALL + labels + sync) reused the same connection; httplib2/
OpenSSL sockets are not thread-safe, so interleaved TLS reads corrupted the
record framing -> WRONG_VERSION_NUMBER (a raw shared-socket race even segfaults).

The fix builds a fresh service (its own connection) per `_gmail()` call, kept
cheap via `static_discovery=True` (bundled discovery doc, no network round-trip).
These tests lock in that structural guarantee deterministically (no network).
"""
import sys
import os

DASHBOARD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

import email_studio


class _FakeCreds:
    expired = False
    refresh_token = None

    @classmethod
    def from_authorized_user_file(cls, path, scopes):
        return cls()


def _install_fakes(monkeypatch):
    """Patch account lookup + google internals so _gmail() runs without network."""
    monkeypatch.setattr(
        email_studio, "_pick_account",
        lambda account_id=None: {"id": "acc-1", "token_path": "/tmp/does-not-matter.json"},
    )
    import google.oauth2.credentials as gcreds
    monkeypatch.setattr(gcreds, "Credentials", _FakeCreds)

    calls = []

    class _FakeService:
        def __init__(self, n):
            self.n = n

    def fake_build(api, version, credentials=None, **kwargs):
        calls.append(kwargs)
        return _FakeService(len(calls))

    import googleapiclient.discovery as disc
    monkeypatch.setattr(disc, "build", fake_build)
    return calls


def test_gmail_builds_fresh_service_each_call(monkeypatch):
    """Two calls for the same account must return DISTINCT services (distinct
    connections) — never a shared cached object."""
    _install_fakes(monkeypatch)
    s1 = email_studio._gmail("acc-1")
    s2 = email_studio._gmail("acc-1")
    assert s1 is not s2, "shared service across calls reintroduces the TLS race"


def test_gmail_uses_static_discovery_no_network(monkeypatch):
    """build() must use the bundled discovery doc so per-call construction is
    cheap (no network) and the cache that caused the bug isn't needed."""
    calls = _install_fakes(monkeypatch)
    email_studio._gmail("acc-1")
    assert calls, "googleapiclient build was not called"
    assert calls[-1].get("static_discovery") is True
    assert calls[-1].get("cache_discovery") is False


def test_gmail_does_not_cache_service(monkeypatch):
    """The legacy per-account service cache must stay empty so connections are
    never reused across threads."""
    _install_fakes(monkeypatch)
    email_studio._gmail("acc-1")
    assert not email_studio._gmail_cache, "_gmail must not cache service objects"
