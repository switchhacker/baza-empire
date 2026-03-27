#!/bin/bash
# Baza Empire — Full AI Stack Setup & Optimization
# Run with: sudo bash scripts/setup-ai-stack.sh
set -e

FW="/home/switchhacker/baza-empire/agent-framework-v3"
VENV="$FW/venv"
USER="switchhacker"

echo "=== Optimizing Ollama AMD (port 11434) ==="
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_VULKAN=1"
Environment="HSA_ENABLE_SDMA=0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KEEP_ALIVE=20m"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_MAX_QUEUE=10"
Environment="OLLAMA_HOST=127.0.0.1:11434"
EOF

echo "=== Optimizing Ollama CUDA (port 11435) ==="
mkdir -p /etc/systemd/system/ollama-cuda.service.d
cat > /etc/systemd/system/ollama-cuda.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11435"
Environment="OLLAMA_MODELS=/usr/share/ollama/.ollama/models"
Environment="OLLAMA_VULKAN=0"
Environment="CUDA_VISIBLE_DEVICES=0"
Environment="GGML_CUDA_VISIBLE_DEVICES=0"
Environment="HSA_VISIBLE_DEVICES="
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KEEP_ALIVE=20m"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_MAX_QUEUE=10"
EOF

echo "=== Installing LiteLLM proxy service ==="
cat > /etc/systemd/system/baza-litellm.service << EOF
[Unit]
Description=Baza Empire — LiteLLM Cloud Proxy (port 4000)
After=network.target
Wants=network.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$FW
Environment=HOME=/home/$USER
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=-$FW/configs/secrets.env
ExecStart=$VENV/bin/litellm --config $FW/configs/litellm.yaml --port 4000 --host 127.0.0.1
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl restart ollama
systemctl restart ollama-cuda
systemctl enable baza-litellm
systemctl start baza-litellm

echo "=== Starting SD WebUI ==="
systemctl start baza-sd-webui

echo ""
echo "Done! Services restarted with optimized settings."
echo "LiteLLM proxy: http://localhost:4000 (OpenAI-compatible API)"
echo "Ollama AMD:    http://localhost:11434"
echo "Ollama CUDA:   http://localhost:11435"
echo "SD WebUI:      http://localhost:7860"
