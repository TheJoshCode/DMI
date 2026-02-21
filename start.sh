#!/bin/bash

echo "╔═══════════════════════════════════════╗"
echo "║         DM-I Startup Script           ║"
echo "╚═══════════════════════════════════════╝"
echo ""

# Ensure directories exist
mkdir -p models data/characters data/storyline data/chroma_db

# ---------------------------------------------------------------------------
# LLM Model Selection — reads from data/llm_config.json if it exists,
# otherwise uses the built-in default. The backend can also manage this
# dynamically via the /api/llm/config endpoint.
# ---------------------------------------------------------------------------
CONFIG_FILE="data/llm_config.json"

DEFAULT_LLM_MODEL="models/nanbeige4.1-3b-q4_k_m.gguf"
DEFAULT_CHAT_TEMPLATE="glm4"
DEFAULT_CONTEXT=8192
DEFAULT_GPU_LAYERS=0
DEFAULT_PORT=8080

if [ -f "$CONFIG_FILE" ]; then
    echo "📋 Loading LLM config from $CONFIG_FILE ..."
    LLM_MODEL=$(python3 -c "import json,sys; d=json.load(open('$CONFIG_FILE')); print(d.get('model_path','$DEFAULT_LLM_MODEL'))" 2>/dev/null || echo "$DEFAULT_LLM_MODEL")
    CHAT_TEMPLATE=$(python3 -c "import json,sys; d=json.load(open('$CONFIG_FILE')); print(d.get('chat_template','$DEFAULT_CHAT_TEMPLATE'))" 2>/dev/null || echo "$DEFAULT_CHAT_TEMPLATE")
    CONTEXT_SIZE=$(python3 -c "import json,sys; d=json.load(open('$CONFIG_FILE')); print(d.get('context_size',$DEFAULT_CONTEXT))" 2>/dev/null || echo "$DEFAULT_CONTEXT")
    GPU_LAYERS=$(python3 -c "import json,sys; d=json.load(open('$CONFIG_FILE')); print(d.get('gpu_layers',$DEFAULT_GPU_LAYERS))" 2>/dev/null || echo "$DEFAULT_GPU_LAYERS")
    LLM_PORT=$(python3 -c "import json,sys; d=json.load(open('$CONFIG_FILE')); print(d.get('server_port',$DEFAULT_PORT))" 2>/dev/null || echo "$DEFAULT_PORT")
    echo "   Model        : $LLM_MODEL"
    echo "   Template     : $CHAT_TEMPLATE"
    echo "   Context      : $CONTEXT_SIZE"
    echo "   GPU layers   : $GPU_LAYERS"
    echo "   LLM port     : $LLM_PORT"
else
    echo "ℹ️  No config file found — using defaults."
    LLM_MODEL="$DEFAULT_LLM_MODEL"
    CHAT_TEMPLATE="$DEFAULT_CHAT_TEMPLATE"
    CONTEXT_SIZE=$DEFAULT_CONTEXT
    GPU_LAYERS=$DEFAULT_GPU_LAYERS
    LLM_PORT=$DEFAULT_PORT
fi

# ---------------------------------------------------------------------------
# Download LLM model if missing (only for the default model)
# ---------------------------------------------------------------------------
if [ ! -f "$LLM_MODEL" ] && [ "$LLM_MODEL" = "$DEFAULT_LLM_MODEL" ]; then
    echo "⚠️  Default LLM not found. Downloading..."
    curl -L -o "$LLM_MODEL" \
        "https://huggingface.co/Edge-Quant/Nanbeige4.1-3B-Q4_K_M-GGUF/resolve/main/nanbeige4.1-3b-q4_k_m.gguf?download=true"
elif [ ! -f "$LLM_MODEL" ]; then
    echo "⚠️  Configured model '$LLM_MODEL' not found."
    echo "   You can download it via the LLM Settings panel in the UI."
    echo "   Falling back to no managed LLM server — configure one via the UI."
    LLM_MODEL=""
fi

# Download embedding model if missing
EMBED_MODEL="models/Qwen3-Embedding-0.6B-Q8_0.gguf"
if [ ! -f "$EMBED_MODEL" ]; then
    echo "⚠️  Embedding model not found. Downloading..."
    curl -L -o "$EMBED_MODEL" \
        "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf?download=true"
fi

# ---------------------------------------------------------------------------
# Clone and build llama.cpp if not present
# ---------------------------------------------------------------------------
if [ ! -f "./llama.cpp/build/bin/llama-server" ]; then
    echo "❌ llama-server not found. Cloning and building llama.cpp..."
    git clone https://github.com/ggerganov/llama.cpp.git llama.cpp
    cd llama.cpp || exit
    mkdir -p build
    cmake -B build -DGGML_CUDA=1
    cmake --build build -j$(( $(nproc) * 75 / 100 )) --config Release
    cd ..
fi

echo "Starting services..."
GLM_PID=""
EMBED_PID=""

# Start LLM server only if we have a model
if [ -n "$LLM_MODEL" ]; then
    echo "🚀 Starting LLM Server (port $LLM_PORT) — $LLM_MODEL"
    ./llama.cpp/build/bin/llama-server \
        -m "$LLM_MODEL" \
        --host 0.0.0.0 \
        --port "$LLM_PORT" \
        -c "$CONTEXT_SIZE" \
        --chat-template "$CHAT_TEMPLATE" \
        -ngl "$GPU_LAYERS" &
    GLM_PID=$!
else
    echo "⚠️  Skipping LLM server (no model). Use the UI to configure one."
fi

echo "🚀 Starting RAG/Embedding server (port 8081)..."
./llama.cpp/build/bin/llama-server \
    -m "$EMBED_MODEL" \
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
echo "🎛️  Use the ⚙️ LLM Settings panel to switch models at runtime."
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

trap "kill $GLM_PID $EMBED_PID $BACKEND_PID 2>/dev/null; exit" INT TERM

wait