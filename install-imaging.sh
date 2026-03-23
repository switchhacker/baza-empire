#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Baza Empire — Sam Axe Imaging Tools Installer
# Installs: rembg (background removal), Pillow, LLaVA vision model in Ollama
# SD WebUI (port 7860) should already be running for generate/enhance/edit
# ─────────────────────────────────────────────────────────────────────────────

set -e
VENV="/home/switchhacker/baza-empire/agent-framework-v3/venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Baza Empire — Sam Imaging Tools Installer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Python imaging deps ───────────────────────────────────────────────────────
echo "[1/3] Installing Python imaging libraries..."
$PIP install --quiet \
    rembg[gpu] \
    Pillow \
    httpx \
    opencv-python-headless \
    numpy

echo "      ✅ rembg, Pillow, opencv installed"

# ── Pull LLaVA vision model ───────────────────────────────────────────────────
echo "[2/3] Pulling LLaVA vision model (for analyze/tag/scan)..."
echo "      This may take a few minutes on first run..."
ollama pull llava:13b && echo "      ✅ llava:13b ready" || \
    ollama pull llava:7b && echo "      ✅ llava:7b ready (fallback)" || \
    echo "      ⚠️  LLaVA pull failed — check Ollama is running on port 11434"

# ── Check SD WebUI ────────────────────────────────────────────────────────────
echo "[3/3] Checking Stable Diffusion WebUI (port 7860)..."
if curl -s --max-time 3 http://localhost:7860/sdapi/v1/sd-models > /dev/null 2>&1; then
    echo "      ✅ SD WebUI is running"
else
    echo "      ⚠️  SD WebUI not detected on port 7860"
    echo "         For generate/enhance/edit tools, ensure stable-diffusion-webui"
    echo "         is running with --api flag:"
    echo "         cd ~/stable-diffusion-webui && ./webui.sh --api --listen"
fi

# ── Restart tool server ───────────────────────────────────────────────────────
echo ""
echo "Restarting baza-tool-server..."
sudo systemctl restart baza-tool-server
sleep 2
sudo systemctl status baza-tool-server --no-pager | tail -3

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Sam imaging tools ready!"
echo ""
echo "  Commands Sam now understands:"
echo "  • 'generate image of a sunset over mountains'"
echo "  • 'analyze image https://...'"
echo "  • 'tag image https://...'"
echo "  • 'enhance image https://...'"
echo "  • 'remove background from https://...'"
echo "  • 'scan photos in /mnt/empirepool/media'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
