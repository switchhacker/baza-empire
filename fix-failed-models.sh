#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Baza Empire — Fix Failed Model Downloads
# Re-downloads the 3 that failed using working URLs
# ─────────────────────────────────────────────────────────────────────────────

CKPT_DIR="/home/switchhacker/stable-diffusion-webui/models/Stable-diffusion"
UPSCALE_DIR="/home/switchhacker/stable-diffusion-webui/models/ESRGAN"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Baza Empire — Fixing failed model downloads"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Clean up 0-byte stub files left by failed downloads
echo "Cleaning 0-byte stubs..."
for f in \
    "$CKPT_DIR/RealVisXL_V5.0_Lightning.safetensors" \
    "$CKPT_DIR/DreamShaper_XL_v2_Turbo.safetensors" \
    "$UPSCALE_DIR/RealESRGAN_x4plus.pth"; do
    if [ -f "$f" ] && [ ! -s "$f" ]; then
        rm -f "$f"
        echo "  🗑️  Removed stub: $(basename $f)"
    fi
done
echo ""

# ── 1. RealVisXL V5 Lightning — via CivitAI (no auth needed) ─────────────────
echo "[1/3] RealVisXL V5 Lightning (~6.5GB) — CivitAI"
DEST="$CKPT_DIR/RealVisXL_V5.0_Lightning.safetensors"
if [ -f "$DEST" ] && [ -s "$DEST" ]; then
    echo "  ✅ Already downloaded"
else
    # CivitAI model version ID 798204 = RealVisXL V5.0 Lightning fp16
    wget -q --show-progress --content-disposition \
        "https://civitai.com/api/download/models/798204?type=Model&format=SafeTensor&size=pruned&fp=fp16" \
        -O "$DEST" && echo "  ✅ Done" || echo "  ❌ Failed"
fi

echo ""

# ── 2. DreamShaper XL v2 Turbo — via CivitAI ─────────────────────────────────
echo "[2/3] DreamShaper XL v2 Turbo (~6.5GB) — CivitAI"
DEST="$CKPT_DIR/DreamShaper_XL_v2_Turbo.safetensors"
if [ -f "$DEST" ] && [ -s "$DEST" ]; then
    echo "  ✅ Already downloaded"
else
    # CivitAI model version ID 351306 = DreamShaper XL v2 Turbo DPM++ SDE
    wget -q --show-progress --content-disposition \
        "https://civitai.com/api/download/models/351306?type=Model&format=SafeTensor" \
        -O "$DEST" && echo "  ✅ Done" || echo "  ❌ Failed"
fi

echo ""

# ── 3. R-ESRGAN 4x+ — via GitHub releases (original repo, no auth) ───────────
echo "[3/3] R-ESRGAN 4x+ (~64MB) — GitHub"
DEST="$UPSCALE_DIR/RealESRGAN_x4plus.pth"
if [ -f "$DEST" ] && [ -s "$DEST" ]; then
    echo "  ✅ Already downloaded"
else
    wget -q --show-progress \
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth" \
        -O "$DEST" && echo "  ✅ Done" || echo "  ❌ Failed"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Final inventory:"
echo ""
echo "  CHECKPOINTS:"
ls -lh "$CKPT_DIR"/*.safetensors 2>/dev/null | awk '{print "  "$5"  "$(NF)}'
echo ""
echo "  UPSCALERS:"
ls -lh "$UPSCALE_DIR"/*.pth 2>/dev/null | awk '{print "  "$5"  "$(NF)}'
echo ""

# Check all models are non-zero
FAILS=0
for f in \
    "$CKPT_DIR/sdxl_base_1.0.safetensors" \
    "$CKPT_DIR/RealVisXL_V5.0_Lightning.safetensors" \
    "$CKPT_DIR/DreamShaper_XL_v2_Turbo.safetensors"; do
    if [ -f "$f" ] && [ -s "$f" ]; then
        echo "  ✅ $(basename $f)"
    else
        echo "  ❌ MISSING: $(basename $f)"
        FAILS=$((FAILS+1))
    fi
done

echo ""
if [ "$FAILS" -eq 0 ]; then
    echo "  ✅ All models good — restarting SD WebUI..."
    sudo systemctl restart baza-sd-webui
    sleep 5
    systemctl is-active --quiet baza-sd-webui && \
        echo "  ✅ SD WebUI up — run: bash check-sd-webui.sh" || \
        echo "  ⚠️  Check logs: journalctl -u baza-sd-webui -n 30 --no-pager"
else
    echo "  ⚠️  $FAILS model(s) still missing"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
