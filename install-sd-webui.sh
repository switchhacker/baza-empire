#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Baza Empire — Stable Diffusion WebUI Installer + Service Setup
# RTX 3070 + RX 6700 XT rig (uses NVIDIA by default, CUDA)
# ─────────────────────────────────────────────────────────────────────────────

set -e

USER_HOME="/home/switchhacker"
SD_DIR="$USER_HOME/stable-diffusion-webui"
SERVICE_NAME="baza-sd-webui"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Baza Empire — SD WebUI Installer"
echo "  RTX 3070 | CUDA | API mode"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. System deps ────────────────────────────────────────────────────────────
echo "[1/6] Installing system dependencies..."
sudo apt-get install -y -q \
    git wget curl python3-pip python3-venv \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxrender1 libxext6 \
    libgoogle-perftools-dev bc
echo "      ✅ System deps ready"

# ── 2. Clone or update SD WebUI ───────────────────────────────────────────────
echo "[2/6] Checking SD WebUI installation..."
if [ -d "$SD_DIR/.git" ]; then
    echo "      Found existing install at $SD_DIR"
    echo "      Pulling latest..."
    cd "$SD_DIR"
    git pull --ff-only || echo "      (skipping pull — local changes present)"
else
    echo "      Cloning AUTOMATIC1111 stable-diffusion-webui..."
    git clone https://github.com/AUTOMATIC1111/stable-diffusion-webui.git "$SD_DIR"
fi
echo "      ✅ SD WebUI source ready"

# ── 3. Download a base model if none present ──────────────────────────────────
echo "[3/6] Checking for SD model..."
MODEL_DIR="$SD_DIR/models/Stable-diffusion"
mkdir -p "$MODEL_DIR"
MODEL_COUNT=$(ls "$MODEL_DIR"/*.safetensors "$MODEL_DIR"/*.ckpt 2>/dev/null | wc -l)
if [ "$MODEL_COUNT" -eq 0 ]; then
    echo "      No model found — downloading SD 1.5 (pruned, safetensors, ~2GB)..."
    wget -q --show-progress \
        "https://huggingface.co/runwayml/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors" \
        -O "$MODEL_DIR/v1-5-pruned-emaonly.safetensors" && \
        echo "      ✅ SD 1.5 model downloaded" || \
        echo "      ⚠️  Model download failed. Download manually to $MODEL_DIR"
else
    echo "      ✅ Model present ($MODEL_COUNT file(s))"
fi

# ── 4. Create the launch wrapper script ───────────────────────────────────────
echo "[4/6] Creating launch wrapper..."
cat > "$SD_DIR/baza-sd-launch.sh" << 'LAUNCH_EOF'
#!/bin/bash
# Baza Empire SD WebUI launcher — used by systemd service
export PYTHONUNBUFFERED=1
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512

cd /home/switchhacker/stable-diffusion-webui

# Activate SD's own venv if it exists, otherwise let webui.sh create it
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

exec python launch.py \
    --api \
    --listen \
    --port 7860 \
    --xformers \
    --enable-insecure-extension-access \
    --no-half-vae \
    --opt-sdp-attention \
    --skip-torch-cuda-test \
    2>&1
LAUNCH_EOF
chmod +x "$SD_DIR/baza-sd-launch.sh"
echo "      ✅ Launch wrapper created"

# ── 5. Create systemd service ─────────────────────────────────────────────────
echo "[5/6] Creating systemd service..."
sudo tee "$SERVICE_FILE" > /dev/null << SERVICE_EOF
[Unit]
Description=Baza Empire — Stable Diffusion WebUI (API)
After=network.target
Wants=network.target

[Service]
Type=simple
User=switchhacker
Group=switchhacker
WorkingDirectory=/home/switchhacker/stable-diffusion-webui
Environment=HOME=/home/switchhacker
Environment=PATH=/usr/local/cuda/bin:/usr/bin:/bin:/home/switchhacker/.local/bin
Environment=PYTHONUNBUFFERED=1
Environment=PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
ExecStart=/home/switchhacker/stable-diffusion-webui/baza-sd-launch.sh
Restart=on-failure
RestartSec=15
TimeoutStartSec=300
StandardOutput=journal
StandardError=journal
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
SERVICE_EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
echo "      ✅ Service created and enabled"

# ── 6. Start it ───────────────────────────────────────────────────────────────
echo "[6/6] Starting SD WebUI service..."
sudo systemctl start "$SERVICE_NAME"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  SD WebUI is starting up (takes 60-120 seconds"
echo "  on first run while it builds the venv)"
echo ""
echo "  Monitor startup:"
echo "  journalctl -u baza-sd-webui -f"
echo ""
echo "  Check when ready:"
echo "  curl -s http://localhost:7860/sdapi/v1/sd-models | python3 -m json.tool | head -20"
echo ""
echo "  Service commands:"
echo "  sudo systemctl status baza-sd-webui"
echo "  sudo systemctl stop baza-sd-webui"
echo "  sudo systemctl restart baza-sd-webui"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
