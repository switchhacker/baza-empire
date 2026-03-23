#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Baza Empire — Curated Model Pack Downloader
# RTX 3070 8GB | SDXL base + fine-tunes + VAE + upscalers
# ─────────────────────────────────────────────────────────────────────────────

SD_DIR="/home/switchhacker/stable-diffusion-webui"
CKPT_DIR="$SD_DIR/models/Stable-diffusion"
VAE_DIR="$SD_DIR/models/VAE"
UPSCALE_DIR="$SD_DIR/models/ESRGAN"
EMBED_DIR="$SD_DIR/embeddings"
HF="https://huggingface.co"

mkdir -p "$CKPT_DIR" "$VAE_DIR" "$UPSCALE_DIR" "$EMBED_DIR"

download() {
    local name="$1" url="$2" dest="$3"
    if [ -f "$dest" ]; then
        echo "  ✅ Already have: $(basename $dest)"
        return 0
    fi
    echo "  ⬇️  Downloading: $name"
    wget -q --show-progress --no-check-certificate -O "$dest" "$url" && \
        echo "      ✅ Done" || \
        echo "      ❌ Failed — try: wget -O $dest '$url'"
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Baza Empire — Model Pack Download"
echo "  RTX 3070 8GB | SDXL optimised"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  1. SDXL 1.0 Base             ~6.5GB  (main engine)"
echo "  2. RealVisXL V5 Lightning    ~6.6GB  (photorealism, 5 steps)"
echo "  3. DreamShaper XL Turbo      ~6.5GB  (art/fantasy/semi-real)"
echo "  4. SDXL VAE                  ~330MB  (fixes washed colours)"
echo "  5. 4x-UltraSharp upscaler     ~67MB  (super-resolution)"
echo "  6. R-ESRGAN 4x+               ~64MB  (general upscaler)"
echo "  7. EasyNegative embedding       ~5KB  (quality boost)"
echo ""
echo "  Total: ~20GB   Free: $(df -h $SD_DIR | tail -1 | awk '{print $4}')"
echo ""
read -p "  Continue? [Y/n] " yn
[[ "$yn" =~ ^[Nn] ]] && echo "Aborted." && exit 0
echo ""

echo "[1/7] SDXL 1.0 Base"
download "SDXL 1.0 Base" \
    "$HF/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" \
    "$CKPT_DIR/sdxl_base_1.0.safetensors"

echo ""
echo "[2/7] RealVisXL V5.0 Lightning — best photorealism, 5 steps"
download "RealVisXL V5 Lightning" \
    "$HF/SG161222/RealVisXL_V5.0_Lightning/resolve/main/RealVisXL_V5.0_Lightning.safetensors" \
    "$CKPT_DIR/RealVisXL_V5.0_Lightning.safetensors"

echo ""
echo "[3/7] DreamShaper XL Turbo — art/fantasy/semi-real"
download "DreamShaper XL v2 Turbo" \
    "$HF/Lykon/dreamshaper-xl-v2-turbo/resolve/main/DreamShaperXL_Turbo_dpmppSDE.safetensors" \
    "$CKPT_DIR/DreamShaper_XL_v2_Turbo.safetensors"

echo ""
echo "[4/7] SDXL VAE — fixes colour issues"
download "SDXL VAE" \
    "$HF/stabilityai/sdxl-vae/resolve/main/sdxl_vae.safetensors" \
    "$VAE_DIR/sdxl_vae.safetensors"

echo ""
echo "[5/7] 4x-UltraSharp upscaler"
download "4x-UltraSharp" \
    "https://civitai.com/api/download/models/125843" \
    "$UPSCALE_DIR/4x-UltraSharp.pth"

echo ""
echo "[6/7] R-ESRGAN 4x+"
download "R-ESRGAN 4x+" \
    "$HF/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus.pth" \
    "$UPSCALE_DIR/RealESRGAN_x4plus.pth"

echo ""
echo "[7/7] EasyNegative embedding"
download "EasyNegative" \
    "$HF/datasets/gsdf/EasyNegative/resolve/main/EasyNegative.safetensors" \
    "$EMBED_DIR/EasyNegative.safetensors"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Inventory:"
echo ""
echo "  CHECKPOINTS:"
ls -lh "$CKPT_DIR"/*.safetensors "$CKPT_DIR"/*.ckpt 2>/dev/null | awk '{print "  "$5"  "$(NF)}'
echo ""
echo "  VAE:"
ls -lh "$VAE_DIR"/*.safetensors 2>/dev/null | awk '{print "  "$5"  "$(NF)}'
echo ""
echo "  UPSCALERS:"
ls -lh "$UPSCALE_DIR"/*.pth 2>/dev/null | awk '{print "  "$5"  "$(NF)}'
echo ""
echo "  Restarting SD WebUI..."
sudo systemctl restart baza-sd-webui
sleep 5
systemctl is-active --quiet baza-sd-webui && \
    echo "  ✅ SD WebUI up — run: bash check-sd-webui.sh" || \
    echo "  ⚠️  Check: journalctl -u baza-sd-webui -n 30 --no-pager"
echo ""
echo "  Sam auto-routes:"
echo "  • photo/portrait/person → RealVisXL V5 Lightning"
echo "  • art/fantasy/logo      → DreamShaper XL Turbo"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
