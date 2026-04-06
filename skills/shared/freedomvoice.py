#!/usr/bin/env python3
"""
Baza Empire Skill — freedomvoice
FreedomVoice phone system integration for 800-484-6404 (AHBCO LLC).
Manages voicemail, call logs, and phone system via SOAP + REST APIs.

SKILL_ARGS:
  action       (str)  — "voicemails", "call_log", "status", "setup_info"
  folder       (str)  — voicemail folder (default: "New")
  limit        (int)  — max results (default: 20)
  download     (bool) — download voicemail MP3s (default: false)

FreedomVoice Credentials:
  Account: contactahbco@gmail.com / 800-484-6404
  SOAP API: https://webservices.freedomvoice.com/freedomapi/freedomapi.asmx
  REST API: https://api.freedomvoice.com

NOTE: The SOAP API requires an account number (not email). The web portal
login (contactahbco@gmail.com) is different from the API username.
To find your API account number:
  1. Log into https://www.freedomvoice.com/weblink
  2. Go to Settings → Account Info → note the Account Number
  3. Set env var: FREEDOMVOICE_ACCOUNT=<number>
  4. Set env var: FREEDOMVOICE_PASSWORD=<api_password>
"""
import os
import sys
import json
import datetime

args = json.loads(os.environ.get("SKILL_ARGS", "{}"))
action = args.get("action", "setup_info")

FV_ACCOUNT = os.environ.get("FREEDOMVOICE_ACCOUNT", "")
FV_PASSWORD = os.environ.get("FREEDOMVOICE_PASSWORD", "Kartina@20")
FV_PHONE = "8004846404"
FV_WSDL = "https://webservices.freedomvoice.com/freedomapi/freedomapi.asmx?WSDL"

ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "dashboard", "artifacts")
VOICE_DIR = os.path.join(ARTIFACTS_DIR, "voice")
os.makedirs(VOICE_DIR, exist_ok=True)


def get_soap_client():
    """Get FreedomVoice SOAP client."""
    try:
        from zeep import Client
        return Client(FV_WSDL)
    except Exception as e:
        return None


def try_login(client):
    """Try to authenticate with FreedomVoice SOAP API."""
    if not FV_ACCOUNT:
        return None, "FREEDOMVOICE_ACCOUNT env var not set. See setup_info for instructions."
    try:
        result = client.service.Login(username=FV_ACCOUNT, password=FV_PASSWORD)
        return result, None
    except Exception as e:
        return None, str(e)


def action_setup_info():
    """Show setup instructions for FreedomVoice integration."""
    info = {
        "phone_number": "800-484-6404",
        "web_portal": "https://www.freedomvoice.com/weblink",
        "web_login": "contactahbco@gmail.com",
        "soap_api": FV_WSDL,
        "rest_api": "https://api.freedomvoice.com",
        "api_account_set": bool(FV_ACCOUNT),
        "status": "configured" if FV_ACCOUNT else "needs_setup",
    }

    setup_steps = """
FreedomVoice Integration Setup for Rex Valor
=============================================

Phone: 800-484-6404
Account: contactahbco@gmail.com

STEP 1: Get API Account Number
  1. Log into https://www.freedomvoice.com/weblink
  2. Go to Settings → Account Info
  3. Note the Account Number (usually a numeric ID)
  4. Add to configs/secrets.env:
     FREEDOMVOICE_ACCOUNT=<your_account_number>

STEP 2: Enable Email Voicemail Delivery
  1. In WebLink → Settings → Message Delivery
  2. Enable "Email Delivery"
  3. Set email to: contactahbco@gmail.com
  4. Include: MP3 attachment, caller ID, date/time

STEP 3: Set Up Call Forwarding to Asterisk (for live AI answering)
  1. Install Asterisk: sudo apt install asterisk
  2. Configure SIP extension in /etc/asterisk/pjsip.conf
  3. In FreedomVoice WebLink → Settings → Call Handling
  4. Add forwarding number pointing to baza's public IP SIP

STEP 4: Voicemail Sync (works now via email)
  1. Configure IMAP access for contactahbco@gmail.com
  2. The freedomvoice skill will poll for voicemail emails
  3. MP3 attachments saved to dashboard/artifacts/voice/
  4. Transcription via cloud vision / whisper
  5. Auto-logged to ahb_voice_logs table

Current Status:
  - SOAP API: {'connected' if get_soap_client() else 'unavailable'}
  - Account configured: {bool(FV_ACCOUNT)}
  - Voicemail dir: {VOICE_DIR}
"""
    print(setup_steps)
    print(json.dumps({"success": True, **info, "message": "Setup instructions printed above"}))


def action_voicemails():
    """Fetch voicemails from FreedomVoice."""
    client = get_soap_client()
    if not client:
        print(json.dumps({"success": False, "error": "Cannot connect to FreedomVoice SOAP API"}))
        sys.exit(1)

    login_result, error = try_login(client)
    if error:
        print(json.dumps({"success": False, "error": error}))
        sys.exit(1)

    try:
        folder = args.get("folder", "New")
        messages = client.service.GetMessageList(
            username=FV_ACCOUNT, password=FV_PASSWORD,
            mailbox="", folder=folder
        )
        print(f"Voicemails in '{folder}' folder:")
        print(json.dumps({"success": True, "folder": folder, "messages": str(messages)}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)


def action_call_log():
    """Fetch call log/CDR from FreedomVoice."""
    client = get_soap_client()
    if not client:
        print(json.dumps({"success": False, "error": "Cannot connect to FreedomVoice SOAP API"}))
        sys.exit(1)

    login_result, error = try_login(client)
    if error:
        print(json.dumps({"success": False, "error": error}))
        sys.exit(1)

    try:
        calls = client.service.GetStaffDIDCalls(
            username=FV_ACCOUNT, password=FV_PASSWORD
        )
        print("Call log:")
        print(json.dumps({"success": True, "calls": str(calls)}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)


def action_status():
    """Check FreedomVoice connection status."""
    client = get_soap_client()
    status = {
        "phone": "800-484-6404",
        "soap_connected": client is not None,
        "account_configured": bool(FV_ACCOUNT),
    }

    if client and FV_ACCOUNT:
        try:
            result = client.service.GetCurrentStatus(
                username=FV_ACCOUNT, password=FV_PASSWORD
            )
            status["api_status"] = str(result)
            status["connected"] = True
        except Exception as e:
            status["api_error"] = str(e)[:200]
            status["connected"] = False
    else:
        status["connected"] = False
        if not FV_ACCOUNT:
            status["note"] = "Set FREEDOMVOICE_ACCOUNT env var — run with action=setup_info for instructions"

    print(f"FreedomVoice Status: {'Connected' if status['connected'] else 'Not connected'}")
    print(f"Phone: {status['phone']}")
    print(json.dumps({"success": True, **status}))


# ── Dispatch ──
if action == "setup_info":
    action_setup_info()
elif action == "voicemails":
    action_voicemails()
elif action == "call_log":
    action_call_log()
elif action == "status":
    action_status()
else:
    print(json.dumps({"success": False, "error": f"Unknown action: {action}. Use: setup_info, voicemails, call_log, status"}))
    sys.exit(1)
