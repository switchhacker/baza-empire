#!/usr/bin/env python3
"""
Interactive iCloud account setup for Baza ingest.

Usage:
  venv/bin/python scripts/icloud_setup.py            # admin (Serge) account
  venv/bin/python scripts/icloud_setup.py --user 5   # for cloud user id 5

Steps:
  1. Prompts for Apple ID + app-specific password
  2. Calls icloudpd in --auth-only mode to bake the cookie + handle 2FA
  3. Registers the account in cloud_icloud_accounts
  4. Runs the first sync (recent 50 photos) so you see something show up immediately
"""
import os, sys, getpass, subprocess, argparse

FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK_DIR)

from core.icloud_ingest import (
    add_account, ingest_account, ICLOUDPD, list_accounts, _get_account
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--user", type=int, default=None, help="Cloud user id (omit for admin)")
    p.add_argument("--ahb-owner", action="store_true", default=None,
                   help="Mark this account as owning AHB jobsite photos")
    p.add_argument("--apple-id", help="Apple ID (will prompt if omitted)")
    p.add_argument("--password", help="App-specific password (will prompt if omitted)")
    p.add_argument("--no-sync", action="store_true", help="Skip the first sync")
    args = p.parse_args()

    print("=" * 60)
    print("  Baza iCloud Ingest — Setup")
    print("=" * 60)
    if args.user is None:
        print("Mode: ADMIN (Serge) — single-tenant")
    else:
        print(f"Mode: Cloud user #{args.user}")
    print()
    print("Before continuing, generate an APP-SPECIFIC PASSWORD at:")
    print("  https://account.apple.com/account/manage  →  Sign-In and Security  →  App-Specific Passwords")
    print("(Your real Apple ID password will NOT work — you must use an app-specific one.)")
    print()

    apple_id = args.apple_id or input("Apple ID (email): ").strip()
    pwd      = args.password or getpass.getpass("App-specific password: ").strip()
    if not apple_id or not pwd:
        print("ERROR: missing credentials"); sys.exit(1)

    if args.ahb_owner is None and args.user is None:
        ahb_owner = True   # admin defaults to AHB owner
    elif args.ahb_owner is None:
        ans = input("Is this account the AHB jobsite photo source? [y/N] ").strip().lower()
        ahb_owner = ans == "y"
    else:
        ahb_owner = args.ahb_owner

    # 1. Register
    aid = add_account(apple_id, pwd, user_id=args.user, ahb_owner=ahb_owner)
    print(f"\n✓ Account registered (id={aid})")

    acc = _get_account(aid)

    # 2. Auth-only call so cookie + 2FA are handled interactively now
    print("\nRunning icloudpd auth — if 2FA is required, you'll be prompted right here.")
    cmd = [
        ICLOUDPD,
        "--username",          apple_id,
        "--cookie-directory",  acc["cookie_dir"],
        "--directory",         acc["download_dir"],
        "--auth-only",
        "--password-provider", "parameter",
        "-p",                  pwd,
    ]
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"\n⚠ icloudpd auth returned {rc}. You can re-run this script to retry.")
        sys.exit(rc)
    print("✓ Authenticated. Cookie cached.")

    # 3. First sync (recent 50)
    if not args.no_sync:
        print("\nRunning first sync (most recent 50 photos)...")
        result = ingest_account(aid, recent=50)
        print()
        if result.get("ok"):
            print(f"✓ Synced {result.get('new_files',0)} new files")
            print(f"   🏗 {result.get('jobsite',0)} jobsite photos imported into AHB123")
            print(f"   📸 {result.get('personal',0)} personal media routed to Baza Cloud")
            for s in result.get("samples", []):
                print(f"   • {s}")
        else:
            print(f"✗ Sync failed: {result.get('error')}")

    print("\nDone. Cron job 'icloud_ingest.py' will keep this account up to date every 6h.")
    print("All accounts:")
    for a in list_accounts(user_id=args.user, include_admin=(args.user is None)):
        print(f"  [{a['id']}] {a['apple_id']} (user={a['user_id']}) ahb_owner={a['ahb_owner']}")


if __name__ == "__main__":
    main()
