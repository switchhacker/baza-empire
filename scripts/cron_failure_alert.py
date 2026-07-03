#!/usr/bin/env python3
"""Claw Batto — systemd cron failure alert responder.

Installed by `sync-agent-crons.py --target systemd --apply` as the
ExecStart of the baza-cron-alert@.service template unit
(configs/systemd-user/baza-cron-alert@.service). Every baza-cron-<name>.service
unit rendered by that script carries `OnFailure=baza-cron-alert@%n.service`
in its [Unit] section, so a failed run starts this template with `%i`
(the failed unit's full name) as argv[1].

Standalone `venv/bin/python` executable -- imports agents/cron_helpers.py via
the same FRAMEWORK_DIR sys.path pattern used by every other cron script
(agents/claw_batto/crons/infra_health.py).

Usage:
    scripts/cron_failure_alert.py <unit>
"""
import os
import sys

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from agents.cron_helpers import send_alert


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: cron_failure_alert.py <unit>", file=sys.stderr)
        return 2

    unit = argv[0]
    message = f"❌ {unit} failed — journalctl --user -u {unit} -n 30"
    send_alert(
        cron_name="systemd",
        message=message,
        alert_key=f"unitfail:{unit}",
        renotify_hours=6,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
