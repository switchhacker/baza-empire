#!/usr/bin/env python3
"""Set or change the passphrase for the Data Hub private gallery.

Run from the framework root:
    venv/bin/python dashboard/set_private_pass.py

Stores a werkzeug password hash at dashboard/.private_pass. Anyone with shell
access to that file can replace the hash, but they cannot read the original
passphrase from it. Add the file to .gitignore (already filtered by the dot
prefix on most templates).
"""
import getpass
import os
import sys

from werkzeug.security import generate_password_hash, check_password_hash

PASS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".private_pass")


def main() -> int:
    if os.path.isfile(PASS_FILE) and os.path.getsize(PASS_FILE) > 0:
        print("A passphrase is already set.")
        current = getpass.getpass("Current passphrase (or empty to overwrite): ")
        if current:
            with open(PASS_FILE, "r", encoding="utf-8") as fh:
                stored = fh.read().strip()
            if not check_password_hash(stored, current):
                print("Wrong passphrase. Aborting.", file=sys.stderr)
                return 1

    new = getpass.getpass("New passphrase: ")
    if not new or len(new) < 4:
        print("Passphrase must be at least 4 characters.", file=sys.stderr)
        return 1
    again = getpass.getpass("Repeat: ")
    if new != again:
        print("Passphrases do not match.", file=sys.stderr)
        return 1

    hashed = generate_password_hash(new)
    with open(PASS_FILE, "w", encoding="utf-8") as fh:
        fh.write(hashed + "\n")
    os.chmod(PASS_FILE, 0o600)
    print(f"Saved hashed passphrase to {PASS_FILE}")
    print("Open /datahub/private in the dashboard to unlock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
