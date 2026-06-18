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
    """16 random bytes as lowercase hex (host-side helper; the R3 makes its own)."""
    return secrets.token_hex(16)


def sign(nonce_hex: str, action: str = "OPEN") -> str:
    """Return HMAC_SHA256(secret, nonce_bytes || b'|' || action) as hex."""
    msg = bytes.fromhex(nonce_hex) + b"|" + action.encode("ascii")
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def verify(nonce_hex: str, token_hex: str, action: str = "OPEN") -> bool:
    """Constant-time check that token_hex is the valid signature for the nonce."""
    try:
        expected = sign(nonce_hex, action)
    except ValueError:
        return False  # malformed nonce hex
    return hmac.compare_digest(expected, token_hex)
