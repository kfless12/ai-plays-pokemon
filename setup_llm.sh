#!/bin/bash
# setup_llm.sh — Install Ollama and pull a small model for Raspberry Pi 5
#
# Usage: bash setup_llm.sh
#
# This script:
# 1. Installs Ollama (if not already installed)
# 2. Starts the Ollama service
# 3. Pulls a small model suitable for Pi 5
#
# Model selection:
#   - 8GB Pi: qwen2.5:3b (best quality that fits)
#   - 4GB Pi: qwen2.5:1.5b (fits with room for mGBA)

set -e

echo "=========================================="
echo "Pokemon AI — LLM Setup for Raspberry Pi 5"
echo "=========================================="

# Check if Ollama is installed
if command -v ollama &> /dev/null; then
    echo "[OK] Ollama is already installed: $(ollama --version)"
else
    echo "[*] Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "[OK] Ollama installed"
fi

# Start Ollama service if not running
if ! pgrep -x "ollama" > /dev/null 2>&1; then
    echo "[*] Starting Ollama service..."
    ollama serve &
    sleep 3
    echo "[OK] Ollama service started"
else
    echo "[OK] Ollama service is already running"
fi

# Detect available RAM
TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
echo "[*] Detected RAM: ${TOTAL_RAM_MB}MB"

if [ "$TOTAL_RAM_MB" -ge 7000 ]; then
    MODEL="qwen2.5:3b"
    echo "[*] 8GB Pi detected — using ${MODEL}"
elif [ "$TOTAL_RAM_MB" -ge 3500 ]; then
    MODEL="qwen2.5:1.5b"
    echo "[*] 4GB Pi detected — using ${MODEL}"
else
    MODEL="qwen2.5:0.5b"
    echo "[*] Low RAM detected — using ${MODEL} (minimal)"
fi

# Pull the model
echo "[*] Pulling model: ${MODEL}"
echo "    This may take a few minutes on first run..."
ollama pull "$MODEL"
echo "[OK] Model ${MODEL} is ready"

# Quick test
echo ""
echo "[*] Running quick test..."
RESPONSE=$(ollama run "$MODEL" "Reply with only the word: OK" 2>/dev/null | head -1)
echo "    Model response: ${RESPONSE}"

echo ""
echo "=========================================="
echo "Setup complete!"
echo "Model: ${MODEL}"
echo ""
echo "To use with the Pokemon AI agent:"
echo "  1. Make sure Ollama is running: ollama serve"
echo "  2. Run the agent: python3 agent.py"
echo ""
echo "The agent will auto-detect the model."
echo "=========================================="
