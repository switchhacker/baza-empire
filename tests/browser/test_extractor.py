import asyncio
import json

import pytest

from browser.extractor import extract, validate

SCHEMA = {
    "type": "object",
    "required": ["vendor", "total"],
    "properties": {
        "vendor": {"type": "string"},
        "total": {"type": "number"},
        "items": {"type": "array", "items": {"type": "string"}},
    },
}


def test_validate_ok():
    assert validate({"vendor": "HD", "total": 9.5, "items": ["a"]}, SCHEMA) == []


def test_validate_catches_missing_and_wrong_type():
    errs = validate({"total": "nine"}, SCHEMA)
    assert any("vendor" in e for e in errs)
    assert any("total" in e for e in errs)


def test_validate_required_field_present_but_null_passes():
    # A correctly-behaving model reporting "not found on page" via null for
    # a required field must NOT be treated as a validation failure.
    assert validate({"vendor": None, "total": 9.5}, SCHEMA) == []


def test_validate_required_field_absent_still_fails():
    errs = validate({"total": 9.5}, SCHEMA)
    assert any("vendor" in e for e in errs)


def test_validate_wrong_type_non_null_value_still_fails():
    errs = validate({"vendor": "HD", "total": "nine"}, SCHEMA)
    assert any("total" in e for e in errs)


def _fake_ollama(monkeypatch, replies):
    """replies: list of message-content strings returned in order."""
    from browser import extractor
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, content):
            self._c = content
        def raise_for_status(self):
            return None
        def json(self):
            return {"message": {"content": self._c}}

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, **kw):
            assert url.endswith("/api/chat")
            content = replies[min(calls["n"], len(replies) - 1)]
            calls["n"] += 1
            return FakeResp(content)

    monkeypatch.setattr(extractor.httpx, "AsyncClient", FakeClient)
    return calls


def test_extract_success_first_try(monkeypatch):
    _fake_ollama(monkeypatch, [json.dumps({"vendor": "HD", "total": 9.5})])
    out = asyncio.run(extract("page text", SCHEMA))
    assert out["success"] is True and out["data"]["vendor"] == "HD"


def test_extract_retries_then_succeeds(monkeypatch):
    calls = _fake_ollama(monkeypatch, [
        json.dumps({"total": 1}),                       # missing vendor → retry
        json.dumps({"vendor": "HD", "total": 1}),
    ])
    out = asyncio.run(extract("page text", SCHEMA))
    assert out["success"] is True and calls["n"] == 2


def test_extract_fails_after_retry(monkeypatch):
    _fake_ollama(monkeypatch, ["not json at all"])
    out = asyncio.run(extract("page text", SCHEMA))
    assert out["success"] is False and "invalid JSON" in out["error"]


def test_extract_schema_invalid_json_both_attempts_fails(monkeypatch):
    # Syntactically valid JSON that fails schema validation on both the
    # original attempt and the retry must produce a clean failure, not a
    # silent/duplicate success or an unrelated error.
    calls = _fake_ollama(monkeypatch, [
        json.dumps({"total": 1}),   # missing required "vendor"
        json.dumps({"total": 2}),   # still missing required "vendor"
    ])
    out = asyncio.run(extract("page text", SCHEMA))
    assert out["success"] is False
    assert "validation failed after retry" in out["error"]
    assert calls["n"] == 2


def test_validate_rejects_root_null():
    errs = validate(None, {"type": "object", "required": ["vendor"]})
    assert errs and "null" in errs[0]
