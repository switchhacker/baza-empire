#!/bin/bash
# Baza Empire — Download top-tier Stable Diffusion models
# These are the best publicly available models for RTX 3070 (8GB VRAM)
set -e

MODELS_DIR="/home/switchhacker/stable-diffusion-webui/models/Stable-diffusion"
LORAS_DIR="/home/switchhacker/stable-diffusion-webui/models/Lora"
VAE_DIR="/home/switchhacker/stable-diffusion-webui/models/VAE"
UPSCALE_DIR="/home/switchhacker/stable-diffusion-webui/models/ESRGAN"

mkdir -p "$MODELS_DIR" "$LORAS_DIR" "$VAE_DIR" "$UPSCALE_DIR"

source /home/switchhacker/baza-empire/agent-framework-v3/venv/bin/activate

echo "=== Downloading top-tier SD models ==="

# ── Juggernaut XL v9 — Best photorealistic SDXL (6.5GB) ───────────────────────
# Best overall quality, faces, architecture, nature
if [ ! -f "$MODELS_DIR/juggernautXL_v9Rdphoto2Lightning.safetensors" ]; then
    echo "Downloading Juggernaut XL v9..."
    python3 -c "
from huggingface_hub import hf_hub_download
import shutil
path = hf_hub_download(
    repo_id='RunDiffusion/Juggernaut-XL-v9',
    filename='Juggernaut-XL-v9-RunDiffusionPhoto2-Lightning-4Steps.safetensors',
    local_dir='/tmp/hf-dl'
)
shutil.move(path, '$MODELS_DIR/juggernautXL_v9Rdphoto2Lightning.safetensors')
print('Done:', path)
"
else
    echo "Juggernaut XL v9 already exists, skipping"
fi

# ── SDXL VAE fix — prevents washed out colors ──────────────────────────────────
if [ ! -f "$VAE_DIR/sdxl_vae.safetensors" ]; then
    echo "Downloading SDXL VAE..."
    python3 -c "
from huggingface_hub import hf_hub_download
import shutil
path = hf_hub_download(
    repo_id='stabilityai/sdxl-vae',
    filename='sdxl_vae.safetensors',
    local_dir='/tmp/hf-dl'
)
shutil.move(path, '$VAE_DIR/sdxl_vae.safetensors')
print('Done')
"
else
    echo "SDXL VAE already exists, skipping"
fi

# ── Real-ESRGAN 4x+ upscaler ───────────────────────────────────────────────────
if [ ! -f "$UPSCALE_DIR/RealESRGAN_x4plus.pth" ]; then
    echo "Downloading Real-ESRGAN 4x upscaler..."
    python3 -c "
from huggingface_hub import hf_hub_download
import shutil
path = hf_hub_download(
    repo_id='ai-forever/Real-ESRGAN',
    filename='RealESRGAN_x4.pth',
    local_dir='/tmp/hf-dl'
)
shutil.move(path, '$UPSCALE_DIR/RealESRGAN_x4plus.pth')
print('Done')
"
else
    echo "Real-ESRGAN already exists, skipping"
fi

echo ""
echo "=== Current SD Models ==="
ls -lh "$MODELS_DIR/"*.safetensors 2>/dev/null || echo "None yet"
echo ""
echo "=== VAE ==="
ls -lh "$VAE_DIR/" 2>/dev/null || echo "None yet"
echo ""
echo "=== Upscalers ==="
ls -lh "$UPSCALE_DIR/" 2>/dev/null || echo "None yet"

echo ""
echo "Done! Restart SD WebUI to pick up new models:"
echo "  systemctl restart baza-sd-webui"
