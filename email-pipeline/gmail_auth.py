#!/usr/bin/env python3
"""
One-time Gmail OAuth setup.
Run this manually once: python gmail_auth.py
Saves token.json to the same directory.
"""
import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
]

DIR     = os.path.dirname(os.path.abspath(__file__))
TOKEN   = os.path.join(DIR, 'token.json')
CREDS   = os.path.join(DIR, 'credentials.json')

def get_credentials():
    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN, 'w') as f:
            f.write(creds.to_json())
        print(f"[gmail_auth] Token saved to {TOKEN}")
    return creds

if __name__ == '__main__':
    creds = get_credentials()
    print(f"[gmail_auth] Authenticated as: checking...")
    import googleapiclient.discovery
    service = googleapiclient.discovery.build('gmail', 'v1', credentials=creds)
    profile = service.users().getProfile(userId='me').execute()
    print(f"[gmail_auth] ✅ Logged in as: {profile['emailAddress']}")
    print(f"[gmail_auth] History ID: {profile['historyId']}")
