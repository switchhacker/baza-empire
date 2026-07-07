"""Seed a logged-in browser profile for Phantom Browser sessions.

RUN ON BAZA'S DESKTOP (needs a display — headed Chromium):

    cd ~/baza-empire/agent-framework-v3
    venv/bin/python -m browser.login_helper gmail https://accounts.google.com

Log in to whatever sites the profile should carry, come back to the terminal
and press Enter. Agents then open sessions with {"profile": "gmail"}.
Only Serge seeds profiles — agents cannot create or modify them."""
import os
import re
import sys

from playwright.sync_api import sync_playwright

try:
    from browser.engine import profiles_dir, UA
except ImportError:  # pragma: no cover
    from engine import profiles_dir, UA


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    name = sys.argv[1]
    if not re.fullmatch(r"[\w\-]+", name):
        print("profile name must be letters/digits/-/_ only")
        return 1
    start_url = sys.argv[2] if len(sys.argv) > 2 else "https://accounts.google.com"
    root = profiles_dir()
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    os.chmod(pdir, 0o700)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(pdir), headless=False, user_agent=UA
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(start_url)
        print(f"\nProfile dir: {pdir}")
        input("Log in in the browser window, then press Enter here to save & close… ")
        ctx.close()
    print(f"Done. Agents can now use sessions with profile: \"{name}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
