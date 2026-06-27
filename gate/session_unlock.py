"""Unlock the local baza login/lock session on a recognized face.

`loginctl unlock-sessions` unlocks the session for the seat (apps/audio resume
immediately), but on GNOME/X11 the visual lock overlay can linger ~15-20s. So we
also nudge GNOME Shell's screensaver to deactivate, which drops the overlay at
once. The nudge is best-effort and never affects the unlock result.
"""
import logging
import os
import subprocess

log = logging.getLogger("baza.gate.session_unlock")


def _gnome_drop_overlay(timeout: float) -> None:
    """Best-effort: tell GNOME Shell to drop the lock overlay immediately."""
    env = dict(os.environ)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    try:
        subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.gnome.ScreenSaver",
             "--object-path", "/org/gnome/ScreenSaver",
             "--method", "org.gnome.ScreenSaver.SetActive", "false"],
            capture_output=True, timeout=timeout, env=env,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        log.warning("gnome overlay dismiss failed (non-fatal): %s", e)


def unlock_session(timeout: float = 5.0) -> bool:
    """Return True if loginctl reported success, False on any failure.

    Also drops the GNOME lock overlay promptly (best-effort, X11 lag workaround).
    """
    try:
        res = subprocess.run(
            ["loginctl", "unlock-sessions"],
            capture_output=True, timeout=timeout,
        )
        ok = res.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        log.warning("session unlock failed: %s", e)
        return False
    _gnome_drop_overlay(timeout)
    return ok
