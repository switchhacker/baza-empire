#!/usr/bin/env python3
"""
One-time Gmail OAuth setup for Baza Empire.
Run this once on baza — it opens a browser, you log in,
and saves a token.json that auto-refreshes forever.
"""

import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

CREDS_PATH = Path(__file__).parents[1] / "configs" / "credentials.json"
TOKEN_PATH = Path(__file__).parents[1] / "configs" / "gmail_token.json"
SECRETS_PATH = Path(__file__).parents[1] / "configs" / "secrets.env"

def main():
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token
        TOKEN_PATH.write_text(creds.to_json())
        print(f"✅ Token saved to {TOKEN_PATH}")

    # Also write refresh token to secrets.env
    token_data = json.loads(TOKEN_PATH.read_text())
    refresh_token = token_data.get("refresh_token", "")
    client_id = token_data.get("client_id", "")
    client_secret = token_data.get("client_secret", "")

    # Update secrets.env
    secrets_lines = []
    if SECRETS_PATH.exists():
        secrets_lines = SECRETS_PATH.read_text().splitlines()

    # Remove old gmail entries
    secrets_lines = [l for l in secrets_lines if not l.startswith("GMAIL_")]

    secrets_lines += [
        f"GMAIL_REFRESH_TOKEN={refresh_token}",
        f"GMAIL_CLIENT_ID={client_id}",
        f"GMAIL_CLIENT_SECRET={client_secret}",
        f"GMAIL_TOKEN_PATH={TOKEN_PATH}",
    ]

    SECRETS_PATH.write_text("\n".join(secrets_lines) + "\n")
    print(f"✅ Gmail credentials written to secrets.env")
    print(f"   Refresh token: {refresh_token[:20]}...")
    print(f"\n🚀 Gmail auth complete! The token auto-refreshes — no maintenance needed.")

if __name__ == "__main__":
    main()
