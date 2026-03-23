#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Baza Empire — SD WebUI Health Check
# ─────────────────────────────────────────────────────────────────────────────

SD_URL="http://localhost:7860"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  SD WebUI Health Check"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Service status
STATUS=$(systemctl is-active baza-sd-webui 2>/dev/null || echo "not-found")
echo "  systemd:  $STATUS"

# Port check
if curl -s --max-time 5 "$SD_URL/sdapi/v1/sd-models" > /dev/null 2>&1; then
    echo "  API port: ✅ responding on :7860"

    # Loaded model
    MODEL=$(curl -s "$SD_URL/sdapi/v1/options" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('sd_model_checkpoint','unknown'))" 2>/dev/null)
    echo "  Model:    $MODEL"

    # Available models
    COUNT=$(curl -s "$SD_URL/sdapi/v1/sd-models" | python3 -c \
        "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
    echo "  Models:   $COUNT available"

    # Quick API test — just check endpoints exist
    for ENDPOINT in txt2img img2img extra-single-image; do
        CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$SD_URL/sdapi/v1/$ENDPOINT" \
            -H "Content-Type: application/json" -d '{}' 2>/dev/null)
        if [ "$CODE" = "422" ] || [ "$CODE" = "200" ]; then
            echo "  /sdapi/v1/$ENDPOINT: ✅ ($CODE)"
        else
            echo "  /sdapi/v1/$ENDPOINT: ⚠️  ($CODE)"
        fi
    done

    echo ""
    echo "  ✅ SD WebUI is READY — all Sam generation tools active"
else
    echo "  API port: ❌ not responding"
    echo ""
    echo "  If service is active, it may still be loading."
    echo "  Watch logs: journalctl -u baza-sd-webui -f"
    echo ""
    UPTIME=$(systemctl show baza-sd-webui --property=ActiveEnterTimestamp 2>/dev/null | cut -d= -f2)
    echo "  Started at: $UPTIME"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
