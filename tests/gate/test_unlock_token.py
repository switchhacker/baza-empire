import os
import pytest
from gate import unlock_token


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("BAZA_GATE_HMAC_SECRET", "test-secret-do-not-ship")


def test_sign_is_deterministic_for_same_nonce():
    nonce = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    t1 = unlock_token.sign(nonce)
    t2 = unlock_token.sign(nonce)
    assert t1 == t2
    assert len(t1) == 64  # hex of SHA-256


def test_verify_accepts_valid_token():
    nonce = unlock_token.new_nonce()
    token = unlock_token.sign(nonce)
    assert unlock_token.verify(nonce, token) is True


def test_verify_rejects_wrong_nonce_replay():
    nonce_a = unlock_token.new_nonce()
    nonce_b = unlock_token.new_nonce()
    token_a = unlock_token.sign(nonce_a)
    assert unlock_token.verify(nonce_b, token_a) is False


def test_verify_rejects_tampered_token():
    nonce = unlock_token.new_nonce()
    token = unlock_token.sign(nonce)
    bad = ("0" if token[0] != "0" else "1") + token[1:]
    assert unlock_token.verify(nonce, bad) is False


def test_action_is_bound_into_signature():
    nonce = unlock_token.new_nonce()
    open_tok = unlock_token.sign(nonce, action="OPEN")
    other_tok = unlock_token.sign(nonce, action="ARM")
    assert open_tok != other_tok
    assert unlock_token.verify(nonce, open_tok, action="OPEN") is True
    assert unlock_token.verify(nonce, open_tok, action="ARM") is False


def test_new_nonce_is_unique_hex():
    n1, n2 = unlock_token.new_nonce(), unlock_token.new_nonce()
    assert n1 != n2
    assert len(n1) == 32 and int(n1, 16) >= 0  # 16 bytes hex, valid hex


import json
from pathlib import Path

VECTORS = Path(__file__).parent / "vectors" / "hmac_vectors.json"


def test_canonical_vectors_match(monkeypatch):
    data = json.loads(VECTORS.read_text())
    monkeypatch.setenv("BAZA_GATE_HMAC_SECRET", data["secret"])
    for case in data["cases"]:
        got = unlock_token.sign(case["nonce"], action=case["action"])
        assert got == case["token"], f"vector mismatch for {case['nonce']}"
