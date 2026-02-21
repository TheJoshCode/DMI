#!/bin/bash

echo "╔═══════════════════════════════════════╗"
echo "║         DM-I Startup Script           ║"
echo "╚═══════════════════════════════════════╝"
echo ""

#if [ ! -f "models/nanbeige4.1-3b-q4_k_m.gguf" ]; then
if [ ! -f "models/GLM-4.7-Flash-MXFP4_MOE.gguf" ]; then
    echo "⚠️  LLM not found!"
    echo "Please download it from:"
    echo "https://huggingface.co/Edge-Quant/Nanbeige4.1-3B-Q4_K_M-GGUF/resolve/main/nanbeige4.1-3b-q4_k_m.gguf?download=true"
    echo ""
fi

if [ ! -f "models/Qwen3-Embedding-0.6B-Q8_0.gguf" ]; then
    echo "⚠️  Embedding model not found!"
    echo "Please download it from https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf?download=true"
    echo ""
fi

mkdir -p data/characters data/storyline data/chroma_db

if ! command -v ./llama.cpp/build/bin/llama-server &> /dev/null; then
    echo "❌ llama-server not found!"
    echo "Please build llama.cpp and place llama-server in this directory"
    exit 1
fi

echo "Starting LLM servers..."

echo "🚀 Starting LLM Server on port 8080..."
./llama.cpp/build/bin/llama-server \
    #-m models/nanbeige4.1-3b-q4_k_m.gguf \
    -m models/GLM-4.7-Flash-MXFP4_MOE.gguf \
    --host 0.0.0.0 \
    --port 8080 \
    -c 8192 \
    --chat-template glm4 \
    -ngl 999 &
GLM_PID=$!

echo "🚀 Starting RAG server on port 8081..."
./llama.cpp/build/bin/llama-server \
    -m models/Qwen3-Embedding-0.6B-Q8_0.gguf \
    --host 0.0.0.0 \
    --port 8081 \
    --embedding \
    -ngl 999 &
EMBED_PID=$!

echo "⏳ Waiting for servers to initialize..."
sleep 10

echo "🐍 Starting DM-I Backend on port 8000..."

uv sync
uv run main.py &
BACKEND_PID=$!

echo ""
echo "✅ All services started!"
echo ""
echo "📍 Access DM-I at: http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

trap "kill $GLM_PID $EMBED_PID $BACKEND_PID 2>/dev/null; exit" INT TERM

wait