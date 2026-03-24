#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Baza Empire — Fix Failed Model Downloads
# Uses huggingface-cli (handles Xet storage, no auth needed for public models)
# ─────────────────────────────────────────────────────────────────────────────

CKPT_DIR="/home/switchhacker/stable-diffusion-webui/models/Stable-diffusion"
UPSCALE_DIR="/home/switchhacker/stable-diffusion-webui/models/ESRGAN"
VENV="/home/switchhacker/baza-empire/agent-framework-v3/venv"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Baza Empire — Fix failed model downloads"
echo "  Using huggingface-cli (handles Xet storage)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Clean 0-byte stubs
echo "Cleaning 0-byte stubs..."
for f in \
    "$CKPT_DIR/RealVisXL_V5.0_Lightning.safetensors" \
    "$CKPT_DIR/DreamShaper_XL_v2_Turbo.safetensors"; do
    if [ -f "$f" ] && [ ! -s "$f" ]; then
        rm -f "$f"
        echo "  🗑️  Removed: $(basename $f)"
    fi
done
echo ""

# ── Ensure huggingface_hub is installed ───────────────────────────────────────
echo "Checking huggingface_hub..."
if "$VENV/bin/python" -c "import huggingface_hub" 2>/dev/null; then
    echo "  ✅ Already installed"
else
    echo "  Installing..."
    "$VENV/bin/pip" install -q "huggingface_hub[hf_xet]"
    echo "  ✅ Done"
fi
HF_CLI="$VENV/bin/huggingface-cli"
# fallback to system if venv doesn't have it
[ -f "$HF_CLI" ] || HF_CLI="$(which huggingface-cli 2>/dev/null || echo '')"
[ -z "$HF_CLI" ] && "$VENV/bin/pip" install -q "huggingface_hub[hf_xet]" && HF_CLI="$VENV/bin/huggingface-cli"
echo ""

# ── Download helper using huggingface_hub python API ─────────────────────────
download_hf() {
    local repo="$1"
    local filename="$2"
    local dest="$3"

    if [ -f "$dest" ] && [ -s "$dest" ]; then
        echo "  ✅ Already have: $(basename $dest)"
        return 0
    fi

    echo "  ⬇️  $repo → $(basename $dest)"
    "$VENV/bin/python" - << PYEOF
import sys
from huggingface_hub import hf_hub_download
import shutil, os

try:
    path = hf_hub_download(
        repo_id="$repo",
        filename="$filename",
        local_dir="/tmp/hf_downloads",
        local_dir_use_symlinks=False,
    )
    shutil.move(path, "$dest")
    size = os.path.getsize("$dest") / (1024**3)
    print(f"  ✅ Done ({size:.2f} GB)")
except Exception as e:
    print(f"  ❌ Failed: {e}")
    sys.exit(1)
PYEOF
}

# ── 1. RealVisXL V5 Lightning fp16 (~6.9GB) ──────────────────────────────────
echo "[1/2] RealVisXL V5.0 Lightning fp16"
download_hf \
    "SG161222/RealVisXL_V5.0_Lightning" \
    "RealVisXL_V5.0_Lightning_fp16.safetensors" \
    "$CKPT_DIR/RealVisXL_V5.0_Lightning.safetensors"

echo ""

# ── 2. DreamShaper XL v2 Turbo (~6.9GB) ──────────────────────────────────────
echo "[2/2] DreamShaper XL v2 Turbo"
download_hf \
    "Lykon/dreamshaper-xl-v2-turbo" \
    "DreamShaperXL_Turbo_v2_1.safetensors" \
    "$CKPT_DIR/DreamShaper_XL_v2_Turbo.safetensors"

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
    echo "  All models ready — restarting SD WebUI..."
    sudo systemctl restart baza-sd-webui
    sleep 5
    systemctl is-active --quiet baza-sd-webui && \
        echo "  ✅ SD WebUI up — run: bash check-sd-webui.sh" || \
        echo "  ⚠️  Check: journalctl -u baza-sd-webui -n 30 --no-pager"
else
    echo "  ⚠️  $FAILS model(s) still missing"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
