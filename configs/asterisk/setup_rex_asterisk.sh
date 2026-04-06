#!/bin/bash
# Baza Empire — Asterisk Setup for Rex Valor
# Self-hosted phone system on baza server
# FreedomVoice 800-484-6404 → forwards to Asterisk → Rex answers
#
# Run: sudo bash configs/asterisk/setup_rex_asterisk.sh

set -e

echo "=== Baza Empire — Rex Valor Phone System Setup ==="
echo ""

# Step 1: Install Asterisk
echo "[1/5] Installing Asterisk..."
apt-get update -qq
apt-get install -y asterisk asterisk-core-sounds-en-wav asterisk-moh-opsound-wav
echo "Asterisk installed: $(asterisk -V)"

# Step 2: Backup original configs
echo "[2/5] Backing up original configs..."
cp /etc/asterisk/pjsip.conf /etc/asterisk/pjsip.conf.bak 2>/dev/null || true
cp /etc/asterisk/extensions.conf /etc/asterisk/extensions.conf.bak 2>/dev/null || true

# Step 3: Write PJSIP config (SIP transport + endpoint for FreedomVoice)
echo "[3/5] Writing PJSIP config..."
cat > /etc/asterisk/pjsip.conf << 'PJSIP_EOF'
; Baza Empire — Rex Valor SIP Configuration
; FreedomVoice forwards 800-484-6404 calls here

[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060

[transport-tcp]
type=transport
protocol=tcp
bind=0.0.0.0:5060

; FreedomVoice trunk — accepts incoming calls from FreedomVoice
[freedomvoice]
type=endpoint
context=ahbco-inbound
disallow=all
allow=ulaw
allow=alaw
allow=g722
transport=transport-udp
; Accept calls from any source (FreedomVoice IPs)
; In production, restrict to FreedomVoice SIP IPs for security
aors=freedomvoice
from_user=8004846404

[freedomvoice]
type=aor
max_contacts=5

[freedomvoice]
type=identify
endpoint=freedomvoice
; FreedomVoice SIP IPs — update these with actual FreedomVoice SIP server IPs
; Check FreedomVoice documentation or support for their SIP server addresses
match=0.0.0.0/0

; Internal extension for Rex Valor testing
[rex-internal]
type=endpoint
context=ahbco-inbound
disallow=all
allow=ulaw
allow=alaw
auth=rex-internal-auth
aors=rex-internal
transport=transport-udp

[rex-internal-auth]
type=auth
auth_type=userpass
username=rex
password=baza-rex-2026

[rex-internal]
type=aor
max_contacts=1
PJSIP_EOF

# Step 4: Write dialplan (extensions.conf)
echo "[4/5] Writing dialplan..."
cat > /etc/asterisk/extensions.conf << 'DIALPLAN_EOF'
; Baza Empire — Rex Valor Dialplan
; Handles incoming calls from FreedomVoice (800-484-6404)

[general]
static=yes
writeprotect=yes
autofallthrough=yes

[globals]
VENV=/home/switchhacker/baza-empire/agent-framework-v3/venv/bin/python3
FRAMEWORK=/home/switchhacker/baza-empire/agent-framework-v3
VOICEMAIL_DIR=/home/switchhacker/baza-empire/agent-framework-v3/dashboard/artifacts/voice

[ahbco-inbound]
; Main incoming call handler
exten => _X.,1,NoOp(=== AHBCO Incoming Call: ${CALLERID(num)} ===)
 same => n,Answer()
 same => n,Wait(1)
 ; Play Rex's greeting (pre-generated via edge-tts)
 same => n,Playback(${VOICEMAIL_DIR}/rex_greeting)
 ; Record the caller's message (max 3 minutes, silence detection 5s)
 same => n,Set(RECORDING=${VOICEMAIL_DIR}/call_${STRFTIME(${EPOCH},,%Y%m%d_%H%M%S)}_${CALLERID(num)})
 same => n,Record(${RECORDING}.wav,5,180,k)
 ; Play confirmation
 same => n,Playback(${VOICEMAIL_DIR}/rex_thankyou)
 same => n,Hangup()
 ; After hangup, trigger processing script
 same => h,System(${VENV} ${FRAMEWORK}/skills/shared/process_call.py "${RECORDING}.wav" "${CALLERID(num)}" "${CALLERID(name)}" &)

; Direct extension for testing
exten => 100,1,NoOp(=== Rex Test Extension ===)
 same => n,Answer()
 same => n,Wait(1)
 same => n,Playback(${VOICEMAIL_DIR}/rex_greeting)
 same => n,Record(${VOICEMAIL_DIR}/test_${EPOCH}.wav,5,60,k)
 same => n,Playback(${VOICEMAIL_DIR}/rex_thankyou)
 same => n,Hangup()

; Catch-all
exten => _X.,1,NoOp(Unhandled: ${EXTEN})
 same => n,Hangup()
DIALPLAN_EOF

# Step 5: Generate Rex's greeting and thank-you audio
echo "[5/5] Generating Rex voice prompts..."
VOICE_DIR="/home/switchhacker/baza-empire/agent-framework-v3/dashboard/artifacts/voice"
mkdir -p "$VOICE_DIR"

# Use edge-tts to generate greeting
cd /home/switchhacker/baza-empire/agent-framework-v3
source venv/bin/activate

python3 -c "
import subprocess, os
voice_dir = '$VOICE_DIR'

# Generate greeting
greeting = 'Thank you for calling All Home Building Company. We are currently helping other homeowners transform their homes. Please leave your name, phone number, and a brief description of your project after the tone, and we will call you back within two hours. If this is urgent, you can also reach us at our website, a h b 1 2 3 dot com.'
subprocess.run(['edge-tts', '--voice', 'en-US-GuyNeural', '--text', greeting,
                '--write-media', os.path.join(voice_dir, 'rex_greeting.wav')],
               capture_output=True, timeout=30)
print('Greeting generated')

# Generate thank you
thankyou = 'Thank you for your message. A member of our team will follow up with you shortly. Have a great day.'
subprocess.run(['edge-tts', '--voice', 'en-US-GuyNeural', '--text', thankyou,
                '--write-media', os.path.join(voice_dir, 'rex_thankyou.wav')],
               capture_output=True, timeout=30)
print('Thank you generated')
" 2>/dev/null || echo "Voice generation skipped (edge-tts not available)"

# Enable and start Asterisk
systemctl enable asterisk
systemctl restart asterisk

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Asterisk is running. Next steps:"
echo ""
echo "1. FIREWALL: Open UDP port 5060 (SIP) and 10000-20000 (RTP):"
echo "   sudo ufw allow 5060/udp"
echo "   sudo ufw allow 10000:20000/udp"
echo ""
echo "2. FREEDOMVOICE: Log into https://www.freedomvoice.com/weblink"
echo "   - Go to Settings → Call Handling"
echo "   - Set forwarding to: sip:8004846404@$(hostname -I | awk '{print $1}'):5060"
echo "   - Or forward to baza's public IP if behind NAT"
echo ""
echo "3. TEST: From another SIP client, call extension 100 at $(hostname -I | awk '{print $1}'):5060"
echo "   - Username: rex / Password: baza-rex-2026"
echo ""
echo "4. MONITOR: asterisk -rvvv (live console)"
echo "   Logs: /var/log/asterisk/messages"
echo ""
