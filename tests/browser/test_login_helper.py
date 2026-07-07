"""login_helper: profile-name validation must reject path traversal.

The headed browser can't run in CI, but a bad profile name returns before
Playwright is ever launched — so the security-relevant validation is testable
without a display.
"""
import browser.login_helper as lh


def _run(argv, monkeypatch):
    monkeypatch.setattr(lh.sys, "argv", ["login_helper", *argv])
    return lh.main()


def test_rejects_path_traversal(monkeypatch, capsys):
    for bad in ["../evil", "a/b", "/abs", "..", "a b", "a.b", ""]:
        assert _run([bad], monkeypatch) == 1, f"should reject {bad!r}"


def test_no_name_prints_usage(monkeypatch):
    assert _run([], monkeypatch) == 1
