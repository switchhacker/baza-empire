"""Unlock the local baza login/lock session on a recognized face.

MVP uses `loginctl unlock-sessions` (unlocks all sessions for the seat). The
exact session targeting and any PAM integration is confirmed at host bring-up;
this wrapper is the single place that side-effects the session.
"""
import logging
import subprocess

log = logging.getLogger("baza.gate.session_unlock")


def unlock_session(timeout: float = 5.0) -> bool:
    """Return True if loginctl reported success, False on any failure."""
    try:
        res = subprocess.run(
            ["loginctl", "unlock-sessions"],
            capture_output=True, timeout=timeout,
        )
        return res.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        log.warning("session unlock failed: %s", e)
        return False
