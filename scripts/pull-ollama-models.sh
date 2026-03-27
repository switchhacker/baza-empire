#!/bin/bash
# Baza Empire — Pull optimized Ollama model set
# AMD GPU (port 11434): large context models, coding
# CUDA GPU (port 11435): fast inference models
set -e

AMD="http://localhost:11434"
CUDA="http://localhost:11435"

pull_model() {
    local base=$1
    local model=$2
    echo "Pulling $model from $base..."
    OLLAMA_HOST=$base ollama pull "$model"
}

echo "=== AMD GPU — Large models (12GB VRAM available) ==="
# Best coding model
pull_model "$AMD" "qwen2.5-coder:32b"
# Best general reasoning
pull_model "$AMD" "llama3.3:70b-instruct-q4_K_M"
# Fast vision
pull_model "$AMD" "llava:13b"

echo "=== CUDA GPU — Speed models (8GB VRAM) ==="
# Best fast model for agents
pull_model "$CUDA" "gemma3:12b"
# Best small coder
pull_model "$CUDA" "qwen2.5-coder:7b"
# Ultra fast
pull_model "$CUDA" "llama3.2:3b"

echo ""
echo "=== All local models ==="
echo "AMD (11434):"
OLLAMA_HOST=$AMD ollama list
echo ""
echo "CUDA (11435):"
OLLAMA_HOST=$CUDA ollama list
