"""deSEC DNS API wrapper for the Network tab.

Provides read + write access to nova.ahb123.com RRsets via the deSEC REST API.
All HTTP calls go through the injectable `http` parameter (defaulting to
`_http`) so tests can monkeypatch without making real network requests.

TOKEN CONTRACT: the token is *never* returned, logged, or included in any
audit entry by this module. It flows in only as a function argument and is
placed in the Authorization header — nowhere else.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Optional

DESEC_DOMAIN = "nova.ahb123.com"
_BASE = f"https://desec.io/api/v1/domains/{DESEC_DOMAIN}/rrsets"

# Allowed RR types — intentionally conservative
_ALLOWED_RTYPES = {"A", "AAAA", "CNAME", "TXT", "MX", "NS"}

# RFC-1918 + public IPv4 pattern — requires exactly 4 decimal octets 0-255
_IPV4_RE = re.compile(
    r"^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)


def _http(method: str, url: str, headers: Optional[dict] = None,
          body: Optional[dict] = None) -> tuple[int, Any]:
    """Minimal urllib wrapper.  Returns (status_code, parsed_json_or_None).

    Never raises on HTTP error status — callers get the status back and
    decide what to do.  Timeout is 8 seconds.
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        },
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, None
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, None
    except Exception:
        return -1, None


def cf_zone_status(token: str, http=None) -> dict:
    """GET Cloudflare zone info for ahb123.com.

    Returns {"found": bool, "status": str, "name_servers": list[str]}.
    Never raises — on any HTTP error or missing data returns found=False.

    TOKEN CONTRACT: token is placed only in the Authorization header; it
    never appears in the URL or any returned dict.
    """
    _call = http or _http
    url = "https://api.cloudflare.com/client/v4/zones?name=ahb123.com"
    status, payload = _call(
        "GET",
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    if status != 200 or not isinstance(payload, dict):
        return {"found": False, "status": "", "name_servers": []}
    result = payload.get("result")
    if not isinstance(result, list) or not result:
        return {"found": False, "status": "", "name_servers": []}
    zone = result[0]
    return {
        "found": True,
        "status": zone.get("status", ""),
        "name_servers": zone.get("name_servers", []),
    }


def desec_rrsets(token: str, http=None) -> list[dict]:
    """GET all RRsets for DESEC_DOMAIN.

    Returns a list of {subname, type, ttl, records} dicts.
    Returns [] on any HTTP error so callers can treat it as "no data".
    """
    _call = http or _http
    url = f"{_BASE}/"
    status, payload = _call(
        "GET",
        url,
        headers={"Authorization": f"Token {token}"},
    )
    if status != 200 or not isinstance(payload, list):
        return []
    return [
        {
            "subname": r.get("subname", ""),
            "type": r.get("type", ""),
            "ttl": r.get("ttl"),
            "records": r.get("records", []),
        }
        for r in payload
    ]


def desec_set_rrset(
    token: str,
    subname: str,
    rtype: str,
    ttl: int,
    records: list[str],
    http=None,
) -> dict:
    """PUT (create or replace) a single RRset for DESEC_DOMAIN.

    Raises ValueError for:
      - rtype not in the allowed allowlist
      - ttl < 60
      - rtype == "A" with any record not matching a valid IPv4 address

    Returns the parsed JSON response dict (may be empty on error).
    """
    # --- validation (before any HTTP call) ---
    if subname not in ("", "@") and not re.fullmatch(r"[A-Za-z0-9_]([A-Za-z0-9_.\-]{0,251})?", subname or ""):
        raise ValueError(f"desec_set_rrset: invalid subname {subname!r}")
    if rtype not in _ALLOWED_RTYPES:
        raise ValueError(f"rtype '{rtype}' not in allowed set {_ALLOWED_RTYPES}")
    if not isinstance(ttl, int) or ttl < 60:
        raise ValueError(f"ttl must be an integer >= 60, got {ttl!r}")
    if rtype == "A":
        for rec in records:
            if not _IPV4_RE.match(str(rec)):
                raise ValueError(f"Invalid IPv4 address: {rec!r}")

    _call = http or _http
    # deSEC uses '@' for apex; pass through as-is
    encoded_subname = subname  # deSEC accepts '@' literally in URL
    url = f"{_BASE}/{encoded_subname}/{rtype}/"
    body = {
        "subname": subname,
        "type": rtype,
        "ttl": ttl,
        "records": records,
    }
    _status, payload = _call(
        "PUT",
        url,
        headers={"Authorization": f"Token {token}"},
        body=body,
    )
    if isinstance(payload, dict):
        return payload
    return {}
