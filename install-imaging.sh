#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Baza Empire — Sam Axe Imaging Tools Installer
# ─────────────────────────────────────────────────────────────────────────────

set -e

BASE="/home/switchhacker/baza-empire/agent-framework-v3"
VENV="$BASE/venv"
PIP="$VENV/bin/pip"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Baza Empire — Sam Imaging Tools Installer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Install imaging deps into the tool server venv ───────────────────────────
echo "[1/4] Installing imaging deps into venv..."
$PIP install --quiet \
    "Pillow>=10.0.0" \
    "opencv-python-headless>=4.8.0" \
    "numpy>=1.24.0" \
    "rembg[gpu]>=2.0.50" \
    "httpx~=0.27.0"

echo "      ✅ Pillow, OpenCV, numpy, rembg installed"

# ── Also install tesseract system package for OCR ────────────────────────────
echo "[2/4] Installing tesseract OCR..."
sudo apt-get install -y -q tesseract-ocr tesseract-ocr-eng 2>/dev/null && \
    echo "      ✅ tesseract installed" || \
    echo "      ⚠️  tesseract install failed (OCR will fall back to LLaVA)"

# ── Pull LLaVA vision model ───────────────────────────────────────────────────
echo "[3/4] Checking LLaVA vision model..."
if ollama list 2>/dev/null | grep -q "llava:13b"; then
    echo "      ✅ llava:13b already present"
else
    echo "      Pulling llava:13b..."
    ollama pull llava:13b && echo "      ✅ llava:13b ready" || \
        ollama pull llava:7b && echo "      ✅ llava:7b ready (fallback)" || \
        echo "      ⚠️  LLaVA pull failed"
fi

# ── Check SD WebUI ────────────────────────────────────────────────────────────
echo "[4/4] Checking Stable Diffusion WebUI (port 7860)..."
if curl -s --max-time 3 http://localhost:7860/sdapi/v1/sd-models > /dev/null 2>&1; then
    echo "      ✅ SD WebUI running — generate/enhance/edit ready"
else
    echo "      ⚠️  SD WebUI not running."
    echo "         Generation tools need SD WebUI with --api flag."
    echo "         Analysis/edit/restore tools (OpenCV + LLaVA) work WITHOUT it."
fi

# ── Restart tool server ───────────────────────────────────────────────────────
echo ""
echo "Restarting baza-tool-server..."
sudo systemctl restart baza-tool-server
sleep 3
STATUS=$(systemctl is-active baza-tool-server)
if [ "$STATUS" = "active" ]; then
    echo "✅ baza-tool-server is running"
else
    echo "❌ baza-tool-server failed to start. Check logs:"
    echo "   journalctl -u baza-tool-server -n 30 --no-pager"
    sudo systemctl status baza-tool-server --no-pager -l | tail -20
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Sam now has 49 imaging tools:"
echo ""
echo "  GENERATION (needs SD WebUI on :7860):"
echo "  generate image, variations, inpaint, outpaint,"
echo "  style transfer, sketch-to-image, logo, batch"
echo ""
echo "  ANALYSIS (works now with LLaVA):"
echo "  analyze, tag, OCR, detect objects, detect faces,"
echo "  color palette, similarity, EXIF, NSFW check"
echo ""
echo "  EDITING (works now with OpenCV + Pillow):"
echo "  crop, resize, rotate, flip, watermark, color grade,"
echo "  convert format, collage, GIF"
echo ""
echo "  QUALITY / RESTORATION (works now with OpenCV):"
echo "  denoise, deblur, fix-pixels, restore, auto-enhance,"
echo "  HDR tone map, JPEG fix, bit-depth enhance"
echo "  (colorize, super-res, face-restore need SD WebUI)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
