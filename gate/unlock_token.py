"""HMAC nonce challenge-response for the baza gate strike.

The R3 generates a fresh nonce per presence event and only fires the relay
for an HMAC over *its own* nonce, so a captured/replayed LAN packet cannot
open the door and no clock is needed on the Arduino. The action string is
folded into the MAC so an OPEN token can't be repurposed.
"""
import hashlib
import hmac
import os
import secrets


def _secret() -> bytes:
    s = os.environ.get("BAZA_GATE_HMAC_SECRET")
    if not s:
        raise RuntimeError("BAZA_GATE_HMAC_SECRET is not set")
    return s.encode("utf-8")


def new_nonce() -> str:
    """Return 16 cryptographically-random bytes as lowercase hex (32 chars).

    In production the R3 generates the nonce and sends it in the challenge;
    this helper exists for host-side tests and tooling.
    """
    return secrets.token_hex(16)


def sign(nonce_hex: str, action: str = "OPEN") -> str:
    """Return HMAC_SHA256(secret, nonce_bytes || b'|' || action) as hex."""
    msg = bytes.fromhex(nonce_hex) + b"|" + action.encode("ascii")
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def verify(nonce_hex: str, token_hex: str, action: str = "OPEN") -> bool:
    """Constant-time check that token_hex is the valid signature for the nonce.

    Returns False (never raises) for any malformed input — this guards a
    door-open code path, so a bad/typed packet must deny, not crash.
    """
    if not isinstance(nonce_hex, str) or not isinstance(token_hex, str):
        return False
    try:
        expected = sign(nonce_hex, action)
    except ValueError:
        return False  # malformed nonce hex
    return hmac.compare_digest(expected, token_hex)
