#!/usr/bin/env python3
"""
DM-I: D&D Dungeon Master AI
Combined Launcher + Backend Server
"""

import os
import sys
import json
import asyncio
import base64
import re
import socket
import subprocess
import platform
import time
import threading
import aiofiles
import aiohttp
from typing import List, Dict, Optional, Any
from datetime import datetime
from contextlib import asynccontextmanager
import httpx
import chromadb
from chromadb.config import Settings
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel, Field
import uvicorn

# ---------------------------------------------------------------------------
# PyInstaller path helpers
# ---------------------------------------------------------------------------

def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = _base_dir()

def _rel(*parts: str) -> str:
    return os.path.join(BASE_DIR, *parts)

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX   = platform.system() == "Linux"
IS_MAC     = platform.system() == "Darwin"

def _resolve_llama_bin():
    exe = "llama-server.exe" if IS_WINDOWS else "llama-server"
    candidates = [
        _rel("llama.cpp", "cuda-12.8",    exe),
        _rel("llama.cpp", "build", "bin", exe),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path, os.path.dirname(path)
    if IS_LINUX:
        dest = _rel("llama.cpp", "cuda-12.8", exe)
        return dest, os.path.dirname(dest)
    return (_rel("llama.cpp", "build", "bin", exe),
            _rel("llama.cpp", "build", "bin"))

LLAMA_BIN, LLAMA_LIB_DIR = _resolve_llama_bin()

def _subprocess_env() -> dict:
    env = os.environ.copy()
    if IS_LINUX and os.path.isdir(LLAMA_LIB_DIR):
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = (
            LLAMA_LIB_DIR + (":" + existing if existing else "")
        )
    elif IS_WINDOWS and os.path.isdir(LLAMA_LIB_DIR):
        existing = env.get("PATH", "")
        env["PATH"] = LLAMA_LIB_DIR + (";" + existing if existing else "")
    return env

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG = {
    "llm_server":       "http://localhost:8080",
    "embedding_server": "http://localhost:8081",
    "chroma_path":      _rel("data", "chroma_db"),
    "max_context":      8192,
    "default_temp":     0.7,
    "default_top_p":    0.9,
}

LLM_CONFIG_PATH = _rel("data", "llm_config.json")

DEFAULT_LLM_MODEL   = _rel("models", "Qwen3.5-0.8B-UD-Q8_K_XL.gguf")
DEFAULT_EMBED_MODEL = _rel("models", "Qwen3-Embedding-0.6B-Q8_0.gguf")

DEFAULT_LLM_CONFIG: Dict = {
    "model_path":        DEFAULT_LLM_MODEL,
    "server_port":       8080,
    "chat_template":     "default",
    "context_size":      8192,
    "gpu_layers":        0,
    "managed_by_backend": False,
}

CHAT_TEMPLATES = [
    "glm4", "chatml", "llama2", "llama3", "mistral", "phi3",
    "gemma", "falcon", "alpaca", "vicuna", "openchat", "zephyr",
    "deepseek", "qwen2", "command-r", "default"
]

# ---------------------------------------------------------------------------
# Startup / setup logic
# ---------------------------------------------------------------------------

def _ensure_dirs():
    for d in ("models", _rel("data", "characters"),
              _rel("data", "storyline"), _rel("data", "chroma_db")):
        os.makedirs(_rel(d) if not os.path.isabs(d) else d, exist_ok=True)
    os.makedirs(_rel("models"), exist_ok=True)


def _load_startup_llm_config() -> Dict:
    cfg = dict(DEFAULT_LLM_CONFIG)
    if os.path.exists(LLM_CONFIG_PATH):
        try:
            with open(LLM_CONFIG_PATH) as fh:
                saved = json.load(fh)
            cfg.update(saved)
            print(f"📋 Loaded LLM config: {cfg['model_path']}")
        except Exception as e:
            print(f"⚠  Could not parse llm_config.json: {e} — using defaults")
    else:
        print("ℹ️  No config file — using defaults")
    return cfg


def _download_file(url: str, dest: str, label: str = ""):
    print(f"⚠  {label or dest} not found.  Downloading…")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        import urllib.request
        def _reporthook(count, block_size, total_size):
            if total_size > 0:
                pct = min(100, count * block_size * 100 // total_size)
                print(f"\r   {pct}%", end="", flush=True)
        urllib.request.urlretrieve(url, dest, reporthook=_reporthook)
        print(f"\r   ✓ Saved to {dest}")
    except Exception as e:
        print(f"\n   ✗ Download failed: {e}")


def _ensure_models(cfg: Dict):
    model_path = cfg.get("model_path", DEFAULT_LLM_MODEL)
    if not os.path.exists(model_path):
        if model_path == DEFAULT_LLM_MODEL:
            _download_file(
                "https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B-UD-Q8_K_XL.gguf?download=true",
                model_path,
                label="Default LLM model",
            )
        else:
            print(f"⚠  Configured model '{model_path}' not found.")
            print("   Use the LLM Settings panel in the UI to download it.")
            cfg["model_path"] = ""

    if not os.path.exists(DEFAULT_EMBED_MODEL):
        _download_file(
            "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf?download=true",
            DEFAULT_EMBED_MODEL,
            label="Embedding model",
        )


def _build_llama_cpp():
    bin_path, _ = _resolve_llama_bin()
    if os.path.exists(bin_path):
        return

    import tarfile, zipfile, urllib.request

    def _fetch(url: str, dest: str):
        print(f"⬇  Downloading {os.path.basename(url)} …")
        def _hook(count, block, total):
            if total > 0:
                print(f"\r   {min(100, count * block * 100 // total):3d}%",
                      end="", flush=True)
        urllib.request.urlretrieve(url, dest, reporthook=_hook)
        print()

    if IS_LINUX:
        LINUX_URL = (
            "https://github.com/ai-dock/llama.cpp-cuda/releases/download/"
            "b8298/llama.cpp-b8298-cuda-12.8.tar.gz"
        )
        extract_root = _rel("llama.cpp")
        os.makedirs(extract_root, exist_ok=True)
        archive = _rel("llama_linux.tar.gz")
        _fetch(LINUX_URL, archive)

        print("📦 Extracting Linux archive …")
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(extract_root)
        os.remove(archive)

        cuda_dir = os.path.join(extract_root, "cuda-12.8")
        if os.path.isdir(cuda_dir):
            for fname in os.listdir(cuda_dir):
                fpath = os.path.join(cuda_dir, fname)
                if os.path.isfile(fpath) and not fname.endswith(".so"):
                    os.chmod(fpath, 0o755)

    elif IS_WINDOWS:
        WIN_BIN_URL = (
            "https://github.com/ggml-org/llama.cpp/releases/download/"
            "b8339/llama-b8339-bin-win-cuda-12.4-x64.zip"
        )
        WIN_CUDA_URL = (
            "https://github.com/ggml-org/llama.cpp/releases/download/"
            "b8339/cudart-llama-bin-win-cuda-12.4-x64.zip"
        )
        bin_dir = _rel("llama.cpp", "build", "bin")
        os.makedirs(bin_dir, exist_ok=True)

        for url, tmp in [(WIN_BIN_URL, _rel("llama_win_bin.zip")),
                         (WIN_CUDA_URL, _rel("llama_win_cuda.zip"))]:
            _fetch(url, tmp)
            print(f"📦 Extracting {os.path.basename(tmp)} …")
            with zipfile.ZipFile(tmp, "r") as zf:
                for member in zf.infolist():
                    fname = os.path.basename(member.filename)
                    if not fname:
                        continue
                    out = os.path.join(bin_dir, fname)
                    with zf.open(member) as src, open(out, "wb") as dst:
                        dst.write(src.read())
            os.remove(tmp)

    else:
        print("⚠  Unsupported platform — cannot auto-download llama.cpp.")
        print("   Build manually and place the binary at:", LLAMA_BIN)
        return

    bin_path, _ = _resolve_llama_bin()
    if os.path.exists(bin_path):
        print(f"✓ llama-server ready at {bin_path}")
    else:
        print(f"✗ Binary still not found at {bin_path} — check the archive layout.")


def _wait_for_port(port: int, timeout: float = 30.0, label: str = "") -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                print(f"   ✓ {label or f'Port {port}'} ready")
                return True
        time.sleep(0.5)
    print(f"   ⚠ {label or f'Port {port}'} not ready after {timeout}s")
    return False


def _start_llama_servers(cfg: Dict) -> tuple:
    if not os.path.exists(LLAMA_BIN):
        print("⚠  llama-server binary missing — skipping managed server start.")
        return None, None

    llm_proc = None
    llm_port = cfg.get("server_port", 8080)

    if cfg.get("model_path") and os.path.exists(cfg["model_path"]):
        print(f"🚀 Starting LLM server (port {llm_port})…")
        llm_proc = subprocess.Popen(
            [
                LLAMA_BIN,
                "-m", cfg["model_path"],
                "--host", "0.0.0.0",
                "--port", str(llm_port),
                "-c", str(cfg.get("context_size", 8192)),
                "--chat-template", cfg.get("chat_template", "default"),
                "-ngl", str(cfg.get("gpu_layers", 0)),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_subprocess_env(),
        )
    else:
        print("⚠  Skipping LLM server (no valid model path).")

    print("🚀 Starting RAG/Embedding server (port 8081)…")
    embed_proc = subprocess.Popen(
        [
            LLAMA_BIN,
            "-m", DEFAULT_EMBED_MODEL,
            "--host", "0.0.0.0",
            "--port", "8081",
            "--embedding",
            "-ngl", "17",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_subprocess_env(),
    )

    print("⏳ Waiting for servers to initialise…")
    if llm_proc:
        _wait_for_port(llm_port, timeout=60, label="LLM server")
    _wait_for_port(8081, timeout=60, label="Embedding server")

    return llm_proc, embed_proc


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class Character(BaseModel):
    id: str
    name: str
    player_name: str
    class_type: str
    level: int = 1
    race: str
    background: str
    alignment: str
    stats: Dict[str, int] = Field(default_factory=dict)
    skills: List[str] = Field(default_factory=list)
    equipment: List[str] = Field(default_factory=list)
    spells: List[str] = Field(default_factory=list)
    hp: int = 10
    max_hp: int = 10
    ac: int = 10
    backstory: str = ""
    created_at: str = ""
    updated_at: str = ""


class Message(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None


class ChatRequest(BaseModel):
    character_id: str
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    dice_rolls: List[Dict] = Field(default_factory=list)
    context_used: List[str] = Field(default_factory=list)


class LLMConfigUpdate(BaseModel):
    model_path: str
    chat_template: str = "glm4"
    context_size: int = 8192
    gpu_layers: int = 0
    server_port: int = 8080


class DownloadRequest(BaseModel):
    url: str
    filename: Optional[str] = None
    auto_switch: bool = False
    chat_template: str = "chatml"


class RollbackRequest(BaseModel):
    session_id: str
    turn_index: int


# ---------------------------------------------------------------------------
# Global State
# ---------------------------------------------------------------------------

class DMState:
    def __init__(self):
        self.chroma_client = None
        self.characters_collection = None
        self.storyline_collection = None
        self.sessions: Dict[str, List[Message]] = {}
        self.active_connections: Dict[str, WebSocket] = {}
        self.llm_process: Optional[asyncio.subprocess.Process] = None
        self.llm_config: Dict = dict(DEFAULT_LLM_CONFIG)
        self.download_progress: Dict[str, Dict] = {}

    def load_llm_config(self):
        if os.path.exists(LLM_CONFIG_PATH):
            try:
                with open(LLM_CONFIG_PATH) as fh:
                    saved = json.load(fh)
                self.llm_config.update(saved)
                CONFIG["llm_server"] = f"http://localhost:{self.llm_config['server_port']}"
                print(f"✓ LLM config loaded: {self.llm_config['model_path']}")
            except Exception as e:
                print(f"⚠ Could not load LLM config: {e}")

    def save_llm_config(self):
        os.makedirs(_rel("data"), exist_ok=True)
        with open(LLM_CONFIG_PATH, "w") as fh:
            json.dump(self.llm_config, fh, indent=2)

    async def kill_llm_server(self):
        port = self.llm_config.get("server_port", 8080)

        if self.llm_process is not None:
            pid = self.llm_process.pid
            if self.llm_process.returncode is None:
                print(f"🛑 Terminating tracked llama-server (PID {pid})…")
                try:
                    self.llm_process.terminate()
                    await asyncio.wait_for(self.llm_process.wait(), timeout=5.0)
                    print(f"   ✓ PID {pid} exited cleanly")
                except asyncio.TimeoutError:
                    print(f"   ⚡ Sending SIGKILL to PID {pid}")
                    self.llm_process.kill()
                    try:
                        await asyncio.wait_for(self.llm_process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        print(f"   ✗ PID {pid} still alive after SIGKILL")
            self.llm_process = None

        if not IS_WINDOWS:
            print(f"🔍 Checking for stray processes on port {port}…")
            pids_killed: list = []

            for tool, args in [
                ("fuser",  [f"{port}/tcp"]),
                ("lsof",   ["-ti", f"tcp:{port}"]),
            ]:
                if pids_killed:
                    break
                try:
                    result = await asyncio.create_subprocess_exec(
                        tool, *args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await result.communicate()
                    for raw in stdout.decode().split():
                        try:
                            p = int(raw.strip())
                            os.kill(p, 9)
                            pids_killed.append(p)
                            print(f"   🔪 {tool}: killed PID {p} on port {port}")
                        except (ValueError, ProcessLookupError, PermissionError):
                            pass
                except FileNotFoundError:
                    pass

            if not pids_killed:
                try:
                    await asyncio.create_subprocess_exec(
                        "pkill", "-9", "-f", "llama-server",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    print("   🔪 pkill -9 llama-server (fallback)")
                except FileNotFoundError:
                    pass
        else:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "llama-server.exe"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                pass

        deadline = asyncio.get_event_loop().time() + 8.0
        while asyncio.get_event_loop().time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    print(f"   ✓ Port {port} is now free")
                    return
            await asyncio.sleep(0.4)
        print(f"   ⚠ Port {port} still occupied after 8s — proceeding anyway")

    async def start_llm_server(self):
        if not os.path.exists(LLAMA_BIN):
            print(f"⚠ llama-server binary not found at {LLAMA_BIN}.")
            self.llm_config["managed_by_backend"] = False
            return

        await self.kill_llm_server()

        model_path = self.llm_config["model_path"]
        if not os.path.exists(model_path):
            print(f"⚠ Model not found: {model_path}.")
            self.llm_config["managed_by_backend"] = False
            return

        cmd = [
            LLAMA_BIN,
            "-m", model_path,
            "--host", "0.0.0.0",
            "--port", str(self.llm_config["server_port"]),
            "-c", str(self.llm_config["context_size"]),
            "--chat-template", self.llm_config["chat_template"],
            "-ngl", str(self.llm_config["gpu_layers"]),
        ]

        print(f"🚀 Starting LLM server: {' '.join(cmd)}")
        self.llm_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=_subprocess_env(),
        )
        self.llm_config["managed_by_backend"] = True
        CONFIG["llm_server"] = f"http://localhost:{self.llm_config['server_port']}"
        self.save_llm_config()
        print(f"✓ LLM server started (PID {self.llm_process.pid})")

    async def init_chroma(self):
        self.chroma_client = chromadb.PersistentClient(
            path=CONFIG["chroma_path"],
            settings=Settings(anonymized_telemetry=False),
        )
        self.characters_collection = self.chroma_client.get_or_create_collection(
            name="characters", metadata={"hnsw:space": "cosine"}
        )
        self.storyline_collection = self.chroma_client.get_or_create_collection(
            name="storyline", metadata={"hnsw:space": "cosine"}
        )
        print("✓ ChromaDB initialised")

    def get_embedding(self, text: str) -> List[float]:
        try:
            response = httpx.post(
                f"{CONFIG['embedding_server']}/embedding",
                json={"content": text},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except Exception as e:
            print(f"Embedding error: {e}")
            return [0.0] * 768

    async def add_to_rag(self, collection_name: str, doc_id: str, text: str, metadata: Dict = None):
        embedding = self.get_embedding(text)
        collection = (
            self.characters_collection
            if collection_name == "characters"
            else self.storyline_collection
        )
        collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata or {}],
        )

    async def query_rag(self, collection_name: str, query: str, n_results: int = 3) -> List[Dict]:
        try:
            embedding = self.get_embedding(query)
            collection = (
                self.characters_collection
                if collection_name == "characters"
                else self.storyline_collection
            )
            # FIX: Guard against querying empty collections (causes chromadb error)
            count = collection.count()
            if count == 0:
                return []
            actual_n = min(n_results, count)
            results = collection.query(query_embeddings=[embedding], n_results=actual_n)
            return [
                {"text": doc, "metadata": meta, "distance": dist}
                for doc, meta, dist in zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                )
            ]
        except Exception as e:
            print(f"RAG query error: {e}")
            return []


state = DMState()

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_dirs()
    await state.init_chroma()
    state.load_llm_config()
    yield
    if state.llm_process and state.llm_process.returncode is None:
        state.llm_process.kill()
    print("Shutting down DM-I…")


app = FastAPI(title="DM-I", lifespan=lifespan)

_static_dir = _rel("static")
if os.path.exists(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# ---------------------------------------------------------------------------
# LLM Communication
# ---------------------------------------------------------------------------

async def generate_with_llm(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """Try /v1/chat/completions first (OpenAI-compat), fall back to /completion."""
    try:
        async with httpx.AsyncClient() as client:
            # Prefer the OpenAI-compatible chat endpoint (works with all templates)
            payload_chat = {
                "model": "local",
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
                "stop": ["<|endoftext|>"],
                "repetition_penalty": 1.1,
            }
            resp = await client.post(
                f"{CONFIG['llm_server']}/v1/chat/completions",
                json=payload_chat,
                timeout=120.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception:
        pass

    # Fallback: raw /completion with manual prompt formatting
    try:
        prompt = format_chat_prompt(messages)
        payload = {
            "prompt": prompt,
            "temperature": temperature,
            "top_p": CONFIG["default_top_p"],
            "n_predict": max_tokens,
            "stream": False,
            "stop": ["<|endoftext|>", "DM:"],
            "repeat_penalty": 1.1,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{CONFIG['llm_server']}/completion",
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            return response.json().get("content", "").strip()
    except Exception as e:
        print(f"LLM Error: {e}")
        return f"*The weave of magic falters… (Error: {e})*"


def format_chat_prompt(messages: List[Dict[str, str]]) -> str:
    template = state.llm_config.get("chat_template", "glm4")
    formatted = ""
    for msg in messages:
        role    = msg["role"]
        content = msg["content"]
        if template == "glm4":
            if role == "system":    formatted += f"<|system|>\n{content}<|end|>\n"
            elif role == "user":    formatted += f"<|user|>\n{content}<|end|>\n"
            elif role == "assistant": formatted += f"<|assistant|>\n{content}<|end|>\n"
        elif template in ("chatml", "qwen2", "deepseek"):
            if role == "system":    formatted += f"<|im_start|>system\n{content}<|im_end|>\n"
            elif role == "user":    formatted += f"<|im_start|>user\n{content}<|im_end|>\n"
            elif role == "assistant": formatted += f"<|im_start|>assistant\n{content}<|im_end|>\n"
        elif template == "llama2":
            if role == "system":    formatted += f"[INST] <<SYS>>\n{content}\n<</SYS>>\n\n"
            elif role == "user":    formatted += f"{content} [/INST] "
            elif role == "assistant": formatted += f"{content} </s><s>[INST] "
        elif template == "llama3":
            if role == "system":    formatted += f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n{content}<|eot_id|>"
            elif role == "user":    formatted += f"<|start_header_id|>user<|end_header_id|>\n{content}<|eot_id|>"
            elif role == "assistant": formatted += f"<|start_header_id|>assistant<|end_header_id|>\n{content}<|eot_id|>"
        elif template == "mistral":
            if role == "user":      formatted += f"[INST] {content} [/INST] "
            elif role == "assistant": formatted += f"{content}</s> "
        elif template == "phi3":
            if role == "system":    formatted += f"<|system|>\n{content}<|end|>\n"
            elif role == "user":    formatted += f"<|user|>\n{content}<|end|>\n"
            elif role == "assistant": formatted += f"<|assistant|>\n{content}<|end|>\n"
        elif template == "gemma":
            if role == "user":      formatted += f"<start_of_turn>user\n{content}<end_of_turn>\n"
            elif role == "assistant": formatted += f"<start_of_turn>model\n{content}<end_of_turn>\n"
        else:
            if role == "system":    formatted += f"### System:\n{content}\n\n"
            elif role == "user":    formatted += f"### User:\n{content}\n\n"
            elif role == "assistant": formatted += f"### Assistant:\n{content}\n\n"

    suffixes = {
        "glm4":    "<|assistant|>\n",
        "chatml":  "<|im_start|>assistant\n",
        "qwen2":   "<|im_start|>assistant\n",
        "deepseek":"<|im_start|>assistant\n",
        "llama3":  "<|start_header_id|>assistant<|end_header_id|>\n",
        "phi3":    "<|assistant|>\n",
        "gemma":   "<start_of_turn>model\n",
    }
    formatted += suffixes.get(template, "### Assistant:\n")
    return formatted

# ---------------------------------------------------------------------------
# LLM Management Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/llm/models")
async def list_local_models():
    models_dir = _rel("models")
    os.makedirs(models_dir, exist_ok=True)
    models = []
    for fname in sorted(os.listdir(models_dir)):
        if fname.lower().endswith(".gguf"):
            fpath = os.path.join(models_dir, fname)
            size_bytes = os.path.getsize(fpath)
            models.append({
                "filename": fname,
                "path":     fpath,
                "size_gb":  round(size_bytes / (1024 ** 3), 2),
                "size_bytes": size_bytes,
                "active": (
                    fpath == state.llm_config["model_path"]
                    or fname == os.path.basename(state.llm_config["model_path"])
                ),
            })
    return {"models": models, "models_dir": os.path.abspath(models_dir)}


@app.get("/api/llm/config")
async def get_llm_config():
    return {
        "config":          state.llm_config,
        "chat_templates":  CHAT_TEMPLATES,
        "server_url":      CONFIG["llm_server"],
        "managed_by_backend": state.llm_config.get("managed_by_backend", False),
        "process_running": state.llm_process is not None and state.llm_process.returncode is None,
    }


@app.post("/api/llm/config")
async def update_llm_config(config: LLMConfigUpdate):
    if not os.path.exists(config.model_path):
        raise HTTPException(status_code=400, detail=f"Model file not found: {config.model_path}")
    state.llm_config.update({
        "model_path":    config.model_path,
        "chat_template": config.chat_template,
        "context_size":  config.context_size,
        "gpu_layers":    config.gpu_layers,
        "server_port":   config.server_port,
    })
    state.save_llm_config()
    CONFIG["llm_server"] = f"http://localhost:{config.server_port}"
    await state.start_llm_server()
    return {
        "status":     "success",
        "config":     state.llm_config,
        "server_url": CONFIG["llm_server"],
        "managed":    state.llm_config.get("managed_by_backend", False),
    }


@app.get("/api/llm/status")
async def llm_status():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{CONFIG['llm_server']}/health", timeout=5.0)
            online = resp.status_code == 200
            # FIX: /health may return plain text "OK", not JSON
            try:
                detail = resp.json() if online else {}
            except Exception:
                detail = {"status": resp.text} if online else {}
    except Exception as e:
        online = False
        detail = {"error": str(e)}
    return {
        "online":      online,
        "server_url":  CONFIG["llm_server"],
        "model":       os.path.basename(state.llm_config.get("model_path", "unknown")),
        "detail":      detail,
        "process_running": state.llm_process is not None and state.llm_process.returncode is None,
    }


@app.post("/api/llm/download")
async def download_model(req: DownloadRequest, background_tasks: BackgroundTasks):
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")
    filename = req.filename.strip() if req.filename else url.split("/")[-1].split("?")[0]
    if not filename.lower().endswith(".gguf"):
        filename += ".gguf"
    dest_path = _rel("models", filename)
    if (
        filename in state.download_progress
        and state.download_progress[filename].get("status") == "downloading"
    ):
        return {"status": "already_downloading", "filename": filename}
    state.download_progress[filename] = {
        "downloaded": 0, "total": 0, "percent": 0,
        "status": "starting", "error": None,
        "dest_path": dest_path, "auto_switch": req.auto_switch,
        "chat_template": req.chat_template,
    }
    background_tasks.add_task(
        _download_model_task, url, dest_path, filename, req.auto_switch, req.chat_template
    )
    return {"status": "started", "filename": filename, "dest_path": dest_path}


async def _download_model_task(
    url: str, dest_path: str, filename: str, auto_switch: bool, chat_template: str
):
    prog = state.download_progress[filename]
    prog["status"] = "downloading"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    prog["status"] = "error"
                    prog["error"] = f"HTTP {resp.status}"
                    return
                total = int(resp.headers.get("Content-Length", 0))
                prog["total"] = total
                downloaded = 0
                async with aiofiles.open(dest_path, "wb") as fh:
                    async for chunk in resp.content.iter_chunked(1024 * 256):
                        await fh.write(chunk)
                        downloaded += len(chunk)
                        prog["downloaded"] = downloaded
                        prog["percent"] = round(downloaded / total * 100, 1) if total else 0
        prog["status"]  = "complete"
        prog["percent"] = 100
        print(f"✓ Downloaded: {dest_path}")
        if auto_switch:
            state.llm_config["model_path"]   = dest_path
            state.llm_config["chat_template"] = chat_template
            state.save_llm_config()
            CONFIG["llm_server"] = f"http://localhost:{state.llm_config['server_port']}"
            await state.start_llm_server()
    except Exception as e:
        prog["status"] = "error"
        prog["error"]  = str(e)
        print(f"✗ Download error: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)


@app.get("/api/llm/download/status")
async def download_status():
    return {"downloads": state.download_progress}


@app.get("/api/llm/download/status/{filename}")
async def download_status_single(filename: str):
    if filename not in state.download_progress:
        raise HTTPException(status_code=404, detail="No download record for that file")
    return state.download_progress[filename]

# ---------------------------------------------------------------------------
# Character Management
# ---------------------------------------------------------------------------

DND_CLASSES = [
    "Barbarian", "Bard", "Cleric", "Druid", "Fighter",
    "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer",
    "Warlock", "Wizard", "Artificer", "Blood Hunter",
]
DND_RACES = [
    "Dragonborn", "Dwarf", "Elf", "Gnome", "Half-Elf",
    "Half-Orc", "Halfling", "Human", "Tiefling", "Aasimar",
    "Firbolg", "Goliath", "Kenku", "Tabaxi", "Triton",
]


@app.get("/api/classes")
async def get_classes():
    return {"classes": DND_CLASSES, "races": DND_RACES}


@app.post("/api/character/create")
async def create_character(
    name: str = Form(...),
    player_name: str = Form(...),
    class_type: str = Form(...),
    race: str = Form(...),
    background: str = Form(...),
    alignment: str = Form(...),
    stats: str = Form("{}"),
    backstory: str = Form(""),
):
    # FIX: Derive sensible HP defaults from class
    hp_by_class = {
        "Barbarian": 12, "Fighter": 10, "Paladin": 10, "Ranger": 10,
        "Monk": 8, "Rogue": 8, "Bard": 8, "Cleric": 8, "Druid": 8,
        "Warlock": 8, "Wizard": 6, "Sorcerer": 6, "Artificer": 8, "Blood Hunter": 10,
    }
    base_hp = hp_by_class.get(class_type, 8)
    try:
        stats_dict = json.loads(stats)
    except Exception:
        stats_dict = {}
    con_mod = (stats_dict.get("con", 10) - 10) // 2
    starting_hp = max(1, base_hp + con_mod)

    char_id = f"char_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name.lower().replace(' ', '_')}"
    character = Character(
        id=char_id, name=name, player_name=player_name,
        class_type=class_type, race=race, background=background,
        alignment=alignment, stats=stats_dict, backstory=backstory,
        hp=starting_hp, max_hp=starting_hp,
        created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat(),
    )
    char_path = _rel("data", "characters", f"{char_id}.json")
    with open(char_path, "w") as fh:
        fh.write(character.model_dump_json(indent=2))
    char_text = (
        f"Character: {character.name}\nRace: {character.race}\n"
        f"Class: {character.class_type} {character.level}\n"
        f"Background: {character.background}\nAlignment: {character.alignment}\n"
        f"Backstory: {character.backstory}\nStats: {character.stats}"
    )
    await state.add_to_rag("characters", char_id, char_text,
                            {"name": character.name, "player": character.player_name})
    return {"status": "success", "character": character}


@app.post("/api/character/upload")
async def upload_character_sheet(file: UploadFile = File(...), player_name: str = Form(...)):
    contents = await file.read()
    base64_image = base64.b64encode(contents).decode("utf-8")
    parse_prompt = """You are analysing a D&D 5e character sheet image. Extract and return ONLY a JSON object:
{
    "name": "character name", "race": "race", "class_type": "class", "level": 1,
    "background": "background", "alignment": "alignment",
    "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
    "hp": 10, "ac": 10, "skills": [], "equipment": [], "spells": [], "backstory": ""
}"""
    messages = [
        {"role": "system", "content": "You are a D&D character sheet parser. Respond ONLY with JSON, no other text."},
        {"role": "user", "content": f"Parse this character sheet. {parse_prompt}\n\nImage data: data:image/png;base64,{base64_image[:100]}… [truncated]"},
    ]
    parsed_text = await generate_with_llm(messages, temperature=0.1)
    try:
        json_match = re.search(r'\{.*\}', parsed_text, re.DOTALL)
        if json_match:
            char_data = json.loads(json_match.group())
            char_data["player_name"] = player_name
            char_data["id"] = (
                f"char_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                f"{char_data.get('name','unknown').lower().replace(' ','_')}"
            )
            char_data["created_at"] = char_data["updated_at"] = datetime.now().isoformat()
            character = Character(**char_data)
            char_path = _rel("data", "characters", f"{character.id}.json")
            with open(char_path, "w") as fh:
                fh.write(character.model_dump_json(indent=2))
            char_text = (
                f"Character: {character.name}\nRace: {character.race}\n"
                f"Class: {character.class_type} {character.level}\n"
                f"Backstory: {character.backstory}\nStats: {character.stats}"
            )
            await state.add_to_rag("characters", character.id, char_text,
                                    {"name": character.name, "player": player_name})
            return {"status": "success", "character": character, "parsed": True}
    except Exception as e:
        print(f"Parse error: {e}")
    return {"status": "needs_verification", "raw_parse": parsed_text,
            "message": "Please verify and complete the character details"}


@app.get("/api/character/{character_id}")
async def get_character(character_id: str):
    char_path = _rel("data", "characters", f"{character_id}.json")
    if not os.path.exists(char_path):
        raise HTTPException(status_code=404, detail="Character not found")
    with open(char_path) as fh:
        return json.load(fh)


@app.put("/api/character/{character_id}")
async def update_character(character_id: str, data: dict):
    char_path = _rel("data", "characters", f"{character_id}.json")
    if not os.path.exists(char_path):
        raise HTTPException(status_code=404, detail="Character not found")
    with open(char_path) as fh:
        existing = json.load(fh)
    existing.update(data)
    existing["updated_at"] = datetime.now().isoformat()
    with open(char_path, "w") as fh:
        json.dump(existing, fh, indent=2)
    return {"status": "success", "character": existing}


@app.get("/api/characters")
async def list_characters():
    chars = []
    char_dir = _rel("data", "characters")
    if os.path.exists(char_dir):
        for fname in sorted(os.listdir(char_dir)):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(char_dir, fname)) as fh:
                        chars.append(json.load(fh))
                except Exception:
                    pass  # Skip malformed files
    # Sort by updated_at descending so most recent is first
    chars.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    return {"characters": chars}

# ---------------------------------------------------------------------------
# Storyline Management
# ---------------------------------------------------------------------------

@app.post("/api/storyline/add")
async def add_storyline(
    title: str = Form(...),
    content: str = Form(...),
    category: str = Form("lore"),
):
    story_id = (
        f"story_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
        f"{title.lower().replace(' ', '_')[:20]}"
    )
    await state.add_to_rag(
        "storyline", story_id, f"{title}\n\n{content}",
        {"title": title, "category": category, "created": datetime.now().isoformat()},
    )
    story_path = _rel("data", "storyline", f"{story_id}.json")
    with open(story_path, "w") as fh:
        json.dump({"id": story_id, "title": title, "content": content, "category": category,
                   "created": datetime.now().isoformat()}, fh, indent=2)
    return {"status": "success", "story_id": story_id}


@app.get("/api/storyline")
async def list_storyline():
    entries = []
    story_dir = _rel("data", "storyline")
    if os.path.exists(story_dir):
        for fname in sorted(os.listdir(story_dir)):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(story_dir, fname)) as fh:
                        entries.append(json.load(fh))
                except Exception:
                    pass
    return {"entries": entries}

# ---------------------------------------------------------------------------
# Chat / DM Logic
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are DM-I, an expert Dungeons & Dragons 5th Edition Dungeon Master. Your role is to:

1. Narrate immersive, engaging stories with vivid descriptions
2. Enforce D&D 5e rules accurately but prioritize fun
3. Roleplay NPCs with distinct voices and personalities
4. Manage combat encounters fairly, calling for appropriate rolls
5. Track player stats, HP, and resources
6. Adapt the story based on player choices
7. Use the provided context about characters and storyline

When players attempt actions:
- Ask for appropriate ability checks (d20 + modifier)
- Consider advantage/disadvantage based on circumstances
- Describe successes and failures dramatically
- Award inspiration for good roleplaying

Combat rules:
- Roll initiative at combat start
- Track turn order
- Ask for attack rolls and damage rolls
- Apply AC and resistances correctly
- Describe the action vividly

Always stay in character as the DM. Use second person ("you", "your") when addressing players.
Use dice roll notation like [[1d20+5]] or [[2d6+3]] when asking for rolls.

Current context will be provided below."""


@app.post("/api/session/rollback")
async def rollback_session(req: RollbackRequest):
    sid = req.session_id
    if sid not in state.sessions:
        return {"status": "ok", "turns_remaining": 0}
    keep = req.turn_index * 2
    state.sessions[sid] = state.sessions[sid][:keep]
    return {"status": "ok", "turns_remaining": req.turn_index}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    """Return current session message history."""
    messages = state.sessions.get(session_id, [])
    return {"session_id": session_id, "messages": [m.dict() for m in messages], "turn_count": len(messages) // 2}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    char_path = _rel("data", "characters", f"{request.character_id}.json")
    if not os.path.exists(char_path):
        raise HTTPException(status_code=404, detail="Character not found")
    with open(char_path) as fh:
        character = json.load(fh)

    char_context  = await state.query_rag("characters", request.message, n_results=2)
    story_context = await state.query_rag("storyline",  request.message, n_results=3)

    context_text = "\n\nCHARACTER INFO:\n"
    for ctx in char_context:
        context_text += f"- {ctx['text']}\n"
    context_text += "\n\nSTORYLINE CONTEXT:\n"
    for ctx in story_context:
        context_text += f"- {ctx['text']} (relevance: {1 - ctx['distance']:.2f})\n"

    session_id = request.session_id or f"session_{request.character_id}"

    # Build message list with history
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + context_text},
    ]

    # Inject past conversation (last 10 turns = 20 messages)
    if session_id in state.sessions:
        for msg in state.sessions[session_id][-10:]:
            messages.append({"role": msg.role if msg.role != "assistant" else "assistant",
                              "content": msg.content})

    # Append current user message
    messages.append({
        "role": "user",
        "content": (
            f"I am playing as {character['name']}, a level {character['level']} "
            f"{character['race']} {character['class_type']}. {request.message}"
        ),
    })

    response_text = await generate_with_llm(messages, temperature=0.8)

    dice_pattern = r'\[\[(\d+d\d+(?:[+-]\d+)?)\]\]'
    dice_rolls   = [{"notation": m.group(1), "position": m.start()}
                    for m in re.finditer(dice_pattern, response_text)]
    clean_response = re.sub(dice_pattern, r'\1', response_text)

    if session_id not in state.sessions:
        state.sessions[session_id] = []
    now = datetime.now().isoformat()
    state.sessions[session_id].append(Message(role="user",      content=request.message,   timestamp=now))
    state.sessions[session_id].append(Message(role="assistant", content=clean_response,     timestamp=now))

    # FIX: Trim session history to prevent unbounded memory growth (keep last 50 turns)
    if len(state.sessions[session_id]) > 100:
        state.sessions[session_id] = state.sessions[session_id][-100:]

    return ChatResponse(
        response=clean_response,
        dice_rolls=dice_rolls,
        context_used=[ctx["text"][:100] + "…" for ctx in char_context + story_context],
    )

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    state.active_connections[session_id] = websocket
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "roll":
                await websocket.send_json({
                    "type": "roll_result",
                    "result": data.get("result"),
                    "notation": data.get("notation"),
                })
            elif data.get("type") == "hp_update":
                char_id  = data.get("character_id")
                new_hp   = data.get("hp")
                char_path = _rel("data", "characters", f"{char_id}.json")
                if os.path.exists(char_path):
                    with open(char_path) as fh:
                        char = json.load(fh)
                    char["hp"]         = new_hp
                    char["updated_at"] = datetime.now().isoformat()
                    with open(char_path, "w") as fh:
                        json.dump(char, fh, indent=2)
                    await websocket.send_json({"type": "hp_updated", "hp": new_hp})
            elif data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        state.active_connections.pop(session_id, None)

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse(_rel("static", "index.html"))


@app.get("/app", response_class=HTMLResponse)
async def app_page():
    return FileResponse(_rel("static", "app.html"))

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("""
╔═══════════════════════════════════════╗
║         DM-I Startup Script           ║
╚═══════════════════════════════════════╝
""")

    _ensure_dirs()

    cfg = _load_startup_llm_config()
    _ensure_models(cfg)

    _build_llama_cpp()

    _ext_llm_proc   = None
    _ext_embed_proc = None

    def _launch_servers():
        nonlocal _ext_llm_proc, _ext_embed_proc
        _ext_llm_proc, _ext_embed_proc = _start_llama_servers(cfg)

    server_thread = threading.Thread(target=_launch_servers, daemon=True)
    server_thread.start()
    server_thread.join()

    print("\n🐍 Starting DM-I Backend on port 8000…")
    print("📍 Access DM-I at: http://localhost:8000")
    print("🎛️  Use the ⚙️ LLM Settings panel to switch models at runtime.")
    print("\nPress Ctrl+C to stop all services\n")

    try:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    finally:
        for proc in (_ext_llm_proc, _ext_embed_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print("All services stopped.")


if __name__ == "__main__":
    main()