@echo off
chcp 65001 >nul
echo ╔═══════════════════════════════════════╗
echo ║         DM-I Startup Script           ║
echo ╚═══════════════════════════════════════╝
echo.

if not exist "models\nanbeige4.1-3b-q4_k_m.gguf" (
    echo ⚠️  GLM-4.7-Flash model not found!
    echo Please download from: https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF
    echo.
)

if not exist "models\Qwen3-Embedding-0.6B-Q8_0.gguf" (
    echo ⚠️  Qwen3-Embedding model not found!
    echo.
)

if not exist "data\characters" mkdir data\characters
if not exist "data\storyline" mkdir data\storyline
if not exist "data\chroma_db" mkdir data\chroma_db

echo Starting LLM servers...

echo 🚀 Starting GLM-4.7-Flash on port 8080...
start "GLM Server" cmd /c "llama-server.exe -m models\nanbeige4.1-3b-q4_k_m.gguf --host 0.0.0.0 --port 8080 -c 8192 --chat-template glm4 -ngl 999"

echo 🚀 Starting Qwen3-Embedding on port 8081...
start "Embedding Server" cmd /c "llama-server.exe -m models\Qwen3-Embedding-0.6B-Q8_0.gguf --host 0.0.0.0 --port 8081 --embedding -ngl 999"

echo ⏳ Waiting for servers to initialize...
timeout /t 10 /nobreak >nul

echo 🐍 Starting DM-I Backend...
if not exist "venv" (
    python -m venv venv
)
call venv\Scripts\activate
pip install -q -r requirements.txt

start "DM-I Backend" cmd /k "python main.py"

echo.
echo ✅ All services started!
echo 📍 Access DM-I at: http://localhost:8000
echo.
pause