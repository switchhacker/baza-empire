#!/bin/bash
# Baza Empire — Apply system-level configs (requires sudo)
# Usage: sudo bash scripts/apply-system-config.sh
set -e

FW="/home/switchhacker/baza-empire/agent-framework-v3"
VENV="$FW/venv"
USER="switchhacker"

echo "=== Optimizing Ollama AMD (port 11434, Vulkan/RX6700XT) ==="
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

echo "=== Optimizing Ollama CUDA (port 11435, RTX 3070) ==="
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

echo "=== Installing LiteLLM cloud proxy service (port 4000) ==="
cat > /etc/systemd/system/baza-litellm.service << EOF
[Unit]
Description=Baza Empire — LiteLLM Cloud Proxy (port 4000)
After=network.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$FW
EnvironmentFile=-$FW/configs/secrets.env
ExecStart=$VENV/bin/litellm --config $FW/configs/litellm.yaml --port 4000 --host 127.0.0.1
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "=== Reloading and restarting services ==="
systemctl daemon-reload
systemctl restart ollama
systemctl restart ollama-cuda
systemctl enable baza-litellm
systemctl start baza-litellm

echo ""
echo "=== Status ==="
systemctl is-active ollama && echo "Ollama AMD: OK"
systemctl is-active ollama-cuda && echo "Ollama CUDA: OK"
systemctl is-active baza-litellm && echo "LiteLLM proxy: OK" || echo "LiteLLM proxy: check logs (journalctl -u baza-litellm)"

echo ""
echo "After SD repo clone, run: systemctl start baza-sd-webui"
echo ""
echo "Add cloud keys to: $FW/configs/secrets.env"
echo "  OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, etc."
echo "Then restart: systemctl restart baza-litellm"
