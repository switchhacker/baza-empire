#!/usr/bin/env python3
"""
Approval Auto-Grant — applies a per-action risk policy to pending approvals.

Reads `approval_requested` events from task_events that have been pending
longer than the action's grace window (and never denied), and emits
`approval_granted` so the work proceeds.

Risk classification (lowest = most permissive grace window):
  - ahb.*_update          —  5 min  (low risk, idempotent edits)
  - ahb.*_create          — 10 min
  - baza_proj.delete      — never auto-grant (always require user)
  - baza_proj.run.deploy  — never auto-grant
  - baza_proj.run.flash   — never auto-grant (touches hardware)
  - ahb.*_delete          — never auto-grant
  - everything else       — never auto-grant by default

Recommended cron: */5 * * * *  (every 5 minutes)
Disable per-run via BAZA_APPROVAL_AUTO_GRANT_DISABLED=1
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# Action prefix → grace window in MINUTES (None = never auto-grant)
POLICY: dict[str, int | None] = {
    "ahb.clients_update":   5,
    "ahb.projects_update":  5,
    "ahb.invoices_update":  5,
    "ahb.receipt_update":   5,
    "ahb.payroll_update":   5,
    "ahb.employees_update": 5,
    "ahb.clients_create":   10,
    "ahb.projects_create":  10,
    "ahb.invoices_create":  10,
    "ahb.receipts_create":  10,
    # Deletes, deploys, and flashes are NEVER auto-granted.
    "ahb.clients_delete":      None,
    "ahb.projects_delete":     None,
    "ahb.quote_delete":        None,
    "ahb.invoices_delete":     None,
    "ahb.employees_delete":    None,
    "ahb.blueprints_delete":   None,
    "deploy":                  None,
    "baza_proj.delete":        None,
    "baza_proj.run.deploy":    None,
    "baza_proj.run.flash":     None,
}


def grace_minutes_for(action: str) -> int | None:
    if action in POLICY:
        return POLICY[action]
    # Default: never auto-grant unknown actions (safer)
    return None


def main() -> int:
    if os.environ.get("BAZA_APPROVAL_AUTO_GRANT_DISABLED", "0") in ("1", "true", "yes"):
        print("[auto_grant] disabled via env")
        return 0

    from core import task_events as te
    pending = te.list_approvals(state="pending", limit=300)
    if not pending:
        print("[auto_grant] no pending approvals")
        return 0

    granted = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    for req in pending:
        action = req.get("action") or ""
        grace = grace_minutes_for(action)
        if grace is None:
            skipped += 1
            continue
        ts_str = (req.get("ts") or "").replace(" ", "T")
        try:
            req_ts = datetime.fromisoformat(ts_str)
            if req_ts.tzinfo is None:
                req_ts = req_ts.replace(tzinfo=timezone.utc)
        except Exception:
            skipped += 1
            continue
        age_min = (now - req_ts).total_seconds() / 60.0
        if age_min < grace:
            skipped += 1
            print(f"[auto_grant] {req['id']:>4}  {action:30s}  age {age_min:5.1f}m  "
                  f"(needs {grace}m) — skipping")
            continue
        # Eligible — grant via the dashboard endpoint so re-dispatch happens
        try:
            te.emit("approval_granted",
                    project_id=req.get("project_id"),
                    agent_id=req.get("agent_id"),
                    payload={
                        "action": action,
                        "by": "auto_grant",
                        "grace_minutes": grace,
                        "for_event_id": req["id"],
                    })
            granted += 1
            print(f"[auto_grant] {req['id']:>4}  {action:30s}  age {age_min:5.1f}m  "
                  f"→ AUTO-GRANTED")
        except Exception as e:
            print(f"[auto_grant] emit failed for {req['id']}: {e}")

    print(f"\n[auto_grant] granted={granted} skipped={skipped} total_pending={len(pending)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
