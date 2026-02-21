#!/usr/bin/env python3
"""
DM-I: D&D Dungeon Master AI
Main Backend Server - FastAPI
Handles RAG, character management, and LLM communication
"""

import os
import json
import asyncio
import base64
import re
import subprocess
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

# Configuration
CONFIG = {
    "llm_server": "http://localhost:8080",  # GLM-4.7-Flash
    "embedding_server": "http://localhost:8081",  # Qwen3-Embedding
    "chroma_path": "./data/chroma_db",
    "max_context": 8192,
    "default_temp": 0.7,
    "default_top_p": 0.9,
}

LLM_CONFIG_PATH = "./data/llm_config.json"

DEFAULT_LLM_CONFIG = {
    "model_path": "models/GLM-4.7-Flash-MXFP4_MOE.gguf",
    "server_port": 8080,
    "chat_template": "glm4",
    "context_size": 8192,
    "gpu_layers": 0,
    "managed_by_backend": False,   # True = backend owns the llama-server process
}

CHAT_TEMPLATES = [
    "glm4", "chatml", "llama2", "llama3", "mistral", "phi3",
    "gemma", "falcon", "alpaca", "vicuna", "openchat", "zephyr",
    "deepseek", "qwen2", "command-r", "default"
]

# Data Models
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
    role: str  # system, user, assistant
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

# Global state
class DMState:
    def __init__(self):
        self.chroma_client = None
        self.characters_collection = None
        self.storyline_collection = None
        self.sessions: Dict[str, List[Message]] = {}
        self.active_connections: Dict[str, WebSocket] = {}
        self.llm_process: Optional[asyncio.subprocess.Process] = None
        self.llm_config: Dict = dict(DEFAULT_LLM_CONFIG)
        self.download_progress: Dict[str, Dict] = {}  # filename -> {downloaded, total, status, error}
        
    def load_llm_config(self):
        """Load LLM config from disk, or use defaults"""
        if os.path.exists(LLM_CONFIG_PATH):
            try:
                with open(LLM_CONFIG_PATH, 'r') as f:
                    saved = json.load(f)
                self.llm_config.update(saved)
                CONFIG["llm_server"] = f"http://localhost:{self.llm_config['server_port']}"
                print(f"✓ LLM config loaded: {self.llm_config['model_path']}")
            except Exception as e:
                print(f"⚠ Could not load LLM config: {e}")

    def save_llm_config(self):
        """Persist LLM config to disk"""
        os.makedirs("data", exist_ok=True)
        with open(LLM_CONFIG_PATH, 'w') as f:
            json.dump(self.llm_config, f, indent=2)

    async def start_llm_server(self):
        """Start/restart llama-server with current LLM config"""
        llama_bin = "./llama.cpp/build/bin/llama-server"
        if not os.path.exists(llama_bin):
            print(f"⚠ llama-server binary not found at {llama_bin}. Skipping managed start.")
            self.llm_config["managed_by_backend"] = False
            return

        if self.llm_process and self.llm_process.returncode is None:
            print("🔄 Stopping existing LLM server...")
            self.llm_process.kill()
            try:
                await asyncio.wait_for(self.llm_process.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass

        model_path = self.llm_config["model_path"]
        if not os.path.exists(model_path):
            print(f"⚠ Model not found: {model_path}. LLM server not started.")
            self.llm_config["managed_by_backend"] = False
            return

        cmd = [
            llama_bin,
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
            stderr=asyncio.subprocess.DEVNULL
        )
        self.llm_config["managed_by_backend"] = True
        CONFIG["llm_server"] = f"http://localhost:{self.llm_config['server_port']}"
        self.save_llm_config()
        print(f"✓ LLM server started (PID {self.llm_process.pid})")

    async def init_chroma(self):
        """Initialize ChromaDB for RAG"""
        self.chroma_client = chromadb.PersistentClient(
            path=CONFIG["chroma_path"],
            settings=Settings(anonymized_telemetry=False)
        )
        self.characters_collection = self.chroma_client.get_or_create_collection(
            name="characters",
            metadata={"hnsw:space": "cosine"}
        )
        self.storyline_collection = self.chroma_client.get_or_create_collection(
            name="storyline",
            metadata={"hnsw:space": "cosine"}
        )
        print("✓ ChromaDB initialized")
    
    def get_embedding(self, text: str) -> List[float]:
        """Get embedding from Qwen3 server"""
        try:
            response = httpx.post(
                f"{CONFIG['embedding_server']}/embedding",
                json={"content": text},
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except Exception as e:
            print(f"Embedding error: {e}")
            return [0.0] * 768
    
    async def add_to_rag(self, collection_name: str, doc_id: str, text: str, metadata: Dict = None):
        embedding = self.get_embedding(text)
        collection = self.characters_collection if collection_name == "characters" else self.storyline_collection
        collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata or {}]
        )
    
    async def query_rag(self, collection_name: str, query: str, n_results: int = 3) -> List[Dict]:
        embedding = self.get_embedding(query)
        collection = self.characters_collection if collection_name == "characters" else self.storyline_collection
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results
        )
        return [
            {"text": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0]
            )
        ]

state = DMState()

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("models", exist_ok=True)
    os.makedirs("data/characters", exist_ok=True)
    os.makedirs("data/storyline", exist_ok=True)
    os.makedirs("data/chroma_db", exist_ok=True)
    await state.init_chroma()
    state.load_llm_config()
    yield
    if state.llm_process and state.llm_process.returncode is None:
        state.llm_process.kill()
    print("Shutting down DM-I...")

app = FastAPI(title="DM-I", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ============== LLM Communication ==============

async def llm_response(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    stream: bool = False
) -> str:
    prompt = format_chat_prompt(messages)
    payload = {
        "prompt": prompt,
        "temperature": temperature,
        "top_p": CONFIG["default_top_p"],
        "n_predict": max_tokens,
        "stream": stream,
        "stop": ["<|endoftext|>", "DM:"],
        "repeat_penalty": 1.1,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{CONFIG['llm_server']}/completion",
                json=payload,
                timeout=120.0
            )
            response.raise_for_status()
            result = response.json()
            return result.get("content", "").strip()
    except Exception as e:
        print(f"GLM Error: {e}")
        return f"*The weave of magic falters... (Error: {e})*"

def format_chat_prompt(messages: List[Dict[str, str]]) -> str:
    template = state.llm_config.get("chat_template", "glm4")
    formatted = ""
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if template == "glm4":
            if role == "system":
                formatted += f"<|system|>\n{content}<|end|>\n"
            elif role == "user":
                formatted += f"<|user|>\n{content}<|end|>\n"
            elif role == "assistant":
                formatted += f"<|assistant|>\n{content}<|end|>\n"
        elif template in ("chatml", "qwen2", "deepseek"):
            if role == "system":
                formatted += f"<|im_start|>system\n{content}<|im_end|>\n"
            elif role == "user":
                formatted += f"<|im_start|>user\n{content}<|im_end|>\n"
            elif role == "assistant":
                formatted += f"<|im_start|>assistant\n{content}<|im_end|>\n"
        elif template == "llama2":
            if role == "system":
                formatted += f"[INST] <<SYS>>\n{content}\n<</SYS>>\n\n"
            elif role == "user":
                formatted += f"{content} [/INST] "
            elif role == "assistant":
                formatted += f"{content} </s><s>[INST] "
        elif template == "llama3":
            if role == "system":
                formatted += f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n{content}<|eot_id|>"
            elif role == "user":
                formatted += f"<|start_header_id|>user<|end_header_id|>\n{content}<|eot_id|>"
            elif role == "assistant":
                formatted += f"<|start_header_id|>assistant<|end_header_id|>\n{content}<|eot_id|>"
        elif template == "mistral":
            if role == "user":
                formatted += f"[INST] {content} [/INST] "
            elif role == "assistant":
                formatted += f"{content}</s> "
        elif template == "phi3":
            if role == "system":
                formatted += f"<|system|>\n{content}<|end|>\n"
            elif role == "user":
                formatted += f"<|user|>\n{content}<|end|>\n"
            elif role == "assistant":
                formatted += f"<|assistant|>\n{content}<|end|>\n"
        elif template == "gemma":
            if role == "user":
                formatted += f"<start_of_turn>user\n{content}<end_of_turn>\n"
            elif role == "assistant":
                formatted += f"<start_of_turn>model\n{content}<end_of_turn>\n"
        else:
            # Generic fallback: inject system as first user message
            if role == "system":
                formatted += f"### System:\n{content}\n\n"
            elif role == "user":
                formatted += f"### User:\n{content}\n\n"
            elif role == "assistant":
                formatted += f"### Assistant:\n{content}\n\n"

    # Add generation prompt
    if template == "glm4":
        formatted += "<|assistant|>\n"
    elif template in ("chatml", "qwen2", "deepseek"):
        formatted += "<|im_start|>assistant\n"
    elif template in ("llama3",):
        formatted += "<|start_header_id|>assistant<|end_header_id|>\n"
    elif template == "phi3":
        formatted += "<|assistant|>\n"
    elif template == "gemma":
        formatted += "<start_of_turn>model\n"
    else:
        formatted += "### Assistant:\n"

    return formatted

# ============== LLM Management Endpoints ==============

@app.get("/api/llm/models")
async def list_local_models():
    """List all .gguf files in ./models/ directory"""
    models_dir = "./models"
    os.makedirs(models_dir, exist_ok=True)
    models = []
    for fname in sorted(os.listdir(models_dir)):
        if fname.lower().endswith(".gguf"):
            fpath = os.path.join(models_dir, fname)
            size_bytes = os.path.getsize(fpath)
            size_gb = size_bytes / (1024 ** 3)
            models.append({
                "filename": fname,
                "path": fpath,
                "size_gb": round(size_gb, 2),
                "size_bytes": size_bytes,
                "active": fpath == state.llm_config["model_path"] or fname == os.path.basename(state.llm_config["model_path"])
            })
    return {"models": models, "models_dir": os.path.abspath(models_dir)}

@app.get("/api/llm/config")
async def get_llm_config():
    """Get current LLM configuration"""
    return {
        "config": state.llm_config,
        "chat_templates": CHAT_TEMPLATES,
        "server_url": CONFIG["llm_server"],
        "managed_by_backend": state.llm_config.get("managed_by_backend", False),
        "process_running": state.llm_process is not None and state.llm_process.returncode is None
    }

@app.post("/api/llm/config")
async def update_llm_config(config: LLMConfigUpdate):
    """Update LLM config and restart the managed server (if binary exists)"""
    if not os.path.exists(config.model_path):
        raise HTTPException(status_code=400, detail=f"Model file not found: {config.model_path}")
    
    state.llm_config["model_path"] = config.model_path
    state.llm_config["chat_template"] = config.chat_template
    state.llm_config["context_size"] = config.context_size
    state.llm_config["gpu_layers"] = config.gpu_layers
    state.llm_config["server_port"] = config.server_port
    state.save_llm_config()

    CONFIG["llm_server"] = f"http://localhost:{config.server_port}"

    await state.start_llm_server()
    await asyncio.sleep(3)  # give server a moment to boot

    return {
        "status": "success",
        "config": state.llm_config,
        "server_url": CONFIG["llm_server"],
        "managed": state.llm_config.get("managed_by_backend", False)
    }

@app.get("/api/llm/status")
async def llm_status():
    """Check if the LLM server is reachable and healthy"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{CONFIG['llm_server']}/health", timeout=5.0)
            online = resp.status_code == 200
            detail = resp.json() if online else {}
    except Exception as e:
        online = False
        detail = {"error": str(e)}

    return {
        "online": online,
        "server_url": CONFIG["llm_server"],
        "model": os.path.basename(state.llm_config.get("model_path", "unknown")),
        "detail": detail,
        "process_running": state.llm_process is not None and state.llm_process.returncode is None
    }

@app.post("/api/llm/download")
async def download_model(req: DownloadRequest, background_tasks: BackgroundTasks):
    """Download a GGUF model from a URL into ./models/"""
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    # Derive filename from URL or use provided
    if req.filename:
        filename = req.filename.strip()
    else:
        filename = url.split("/")[-1].split("?")[0]
        if not filename.lower().endswith(".gguf"):
            filename += ".gguf"

    dest_path = f"./models/{filename}"

    if filename in state.download_progress and state.download_progress[filename].get("status") == "downloading":
        return {"status": "already_downloading", "filename": filename}

    state.download_progress[filename] = {
        "downloaded": 0,
        "total": 0,
        "percent": 0,
        "status": "starting",
        "error": None,
        "dest_path": dest_path,
        "auto_switch": req.auto_switch,
        "chat_template": req.chat_template,
    }

    background_tasks.add_task(_download_model_task, url, dest_path, filename, req.auto_switch, req.chat_template)
    return {"status": "started", "filename": filename, "dest_path": dest_path}

async def _download_model_task(url: str, dest_path: str, filename: str, auto_switch: bool, chat_template: str):
    """Background task: stream-download a model file"""
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
                async with aiofiles.open(dest_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 256):  # 256 KB chunks
                        await f.write(chunk)
                        downloaded += len(chunk)
                        prog["downloaded"] = downloaded
                        prog["percent"] = round((downloaded / total * 100), 1) if total else 0
        prog["status"] = "complete"
        prog["percent"] = 100
        print(f"✓ Downloaded: {dest_path}")

        if auto_switch:
            state.llm_config["model_path"] = dest_path
            state.llm_config["chat_template"] = chat_template
            state.save_llm_config()
            CONFIG["llm_server"] = f"http://localhost:{state.llm_config['server_port']}"
            await state.start_llm_server()
    except Exception as e:
        prog["status"] = "error"
        prog["error"] = str(e)
        print(f"✗ Download error: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)

@app.get("/api/llm/download/status")
async def download_status():
    """Get status of all in-progress or completed downloads"""
    return {"downloads": state.download_progress}

@app.get("/api/llm/download/status/{filename}")
async def download_status_single(filename: str):
    """Get status of a single download"""
    if filename not in state.download_progress:
        raise HTTPException(status_code=404, detail="No download record for that file")
    return state.download_progress[filename]

# ============== Character Management ==============

DND_CLASSES = [
    "Barbarian", "Bard", "Cleric", "Druid", "Fighter",
    "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer",
    "Warlock", "Wizard", "Artificer", "Blood Hunter"
]

DND_RACES = [
    "Dragonborn", "Dwarf", "Elf", "Gnome", "Half-Elf",
    "Half-Orc", "Halfling", "Human", "Tiefling", "Aasimar",
    "Firbolg", "Goliath", "Kenku", "Tabaxi", "Triton"
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
    backstory: str = Form("")
):
    char_id = f"char_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name.lower().replace(' ', '_')}"
    character = Character(
        id=char_id,
        name=name,
        player_name=player_name,
        class_type=class_type,
        race=race,
        background=background,
        alignment=alignment,
        stats=json.loads(stats),
        backstory=backstory,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    )
    char_path = f"./data/characters/{char_id}.json"
    with open(char_path, 'w') as f:
        f.write(character.model_dump_json(indent=2))
    char_text = f"Character: {character.name}\nRace: {character.race}\nClass: {character.class_type} {character.level}\nBackground: {character.background}\nAlignment: {character.alignment}\nBackstory: {character.backstory}\nStats: {character.stats}"
    await state.add_to_rag("characters", char_id, char_text, {"name": character.name, "player": character.player_name})
    return {"status": "success", "character": character}

@app.post("/api/character/upload")
async def upload_character_sheet(
    file: UploadFile = File(...),
    player_name: str = Form(...)
):
    contents = await file.read()
    base64_image = base64.b64encode(contents).decode('utf-8')
    parse_prompt = """You are analyzing a D&D 5e character sheet image. Extract the following information and return ONLY a JSON object:
{
    "name": "character name",
    "race": "race",
    "class_type": "class",
    "level": number,
    "background": "background",
    "alignment": "alignment",
    "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
    "hp": number,
    "ac": number,
    "skills": ["skill1", "skill2"],
    "equipment": ["item1", "item2"],
    "spells": ["spell1", "spell2"],
    "backstory": "brief backstory if visible"
}
If any field is unclear, use reasonable defaults or empty strings."""
    messages = [
        {"role": "system", "content": "You are a D&D character sheet parser."},
        {"role": "user", "content": f"Parse this character sheet data (base64 image provided). {parse_prompt}\n\nImage data: data:image/png;base64,{base64_image[:100]}... [truncated]"}
    ]
    parsed_text = await llm_response(messages, temperature=0.1)
    try:
        json_match = re.search(r'\{.*\}', parsed_text, re.DOTALL)
        if json_match:
            char_data = json.loads(json_match.group())
            char_data["player_name"] = player_name
            char_data["id"] = f"char_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{char_data.get('name', 'unknown').lower().replace(' ', '_')}"
            char_data["created_at"] = datetime.now().isoformat()
            char_data["updated_at"] = datetime.now().isoformat()
            character = Character(**char_data)
            char_path = f"./data/characters/{character.id}.json"
            with open(char_path, 'w') as f:
                f.write(character.model_dump_json(indent=2))
            char_text = f"Character: {character.name}\nRace: {character.race}\nClass: {character.class_type} {character.level}\nBackground: {character.background}\nAlignment: {character.alignment}\nBackstory: {character.backstory}\nStats: {character.stats}"
            await state.add_to_rag("characters", character.id, char_text, {"name": character.name, "player": player_name})
            return {"status": "success", "character": character, "parsed": True}
    except Exception as e:
        print(f"Parse error: {e}")
    return {
        "status": "needs_verification",
        "raw_parse": parsed_text,
        "message": "Please verify and complete the character details"
    }

@app.get("/api/character/{character_id}")
async def get_character(character_id: str):
    char_path = f"./data/characters/{character_id}.json"
    if not os.path.exists(char_path):
        raise HTTPException(status_code=404, detail="Character not found")
    with open(char_path, 'r') as f:
        return json.load(f)

@app.put("/api/character/{character_id}")
async def update_character(character_id: str, data: dict):
    """Update character data (HP, AC, stats, etc.)"""
    char_path = f"./data/characters/{character_id}.json"
    if not os.path.exists(char_path):
        raise HTTPException(status_code=404, detail="Character not found")
    with open(char_path, 'r') as f:
        existing = json.load(f)
    existing.update(data)
    existing["updated_at"] = datetime.now().isoformat()
    with open(char_path, 'w') as f:
        json.dump(existing, f, indent=2)
    return {"status": "success", "character": existing}

@app.get("/api/characters")
async def list_characters():
    chars = []
    char_dir = "./data/characters"
    if os.path.exists(char_dir):
        for fname in os.listdir(char_dir):
            if fname.endswith('.json'):
                with open(os.path.join(char_dir, fname), 'r') as f:
                    chars.append(json.load(f))
    return {"characters": chars}

# ============== Storyline Management ==============

@app.post("/api/storyline/add")
async def add_storyline(
    title: str = Form(...),
    content: str = Form(...),
    category: str = Form("lore")
):
    story_id = f"story_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{title.lower().replace(' ', '_')[:20]}"
    await state.add_to_rag(
        "storyline",
        story_id,
        f"{title}\n\n{content}",
        {"title": title, "category": category, "created": datetime.now().isoformat()}
    )
    story_path = f"./data/storyline/{story_id}.json"
    with open(story_path, 'w') as f:
        json.dump({"id": story_id, "title": title, "content": content, "category": category}, f, indent=2)
    return {"status": "success", "story_id": story_id}

@app.get("/api/storyline")
async def list_storyline():
    """List all saved storyline/lore entries"""
    entries = []
    story_dir = "./data/storyline"
    if os.path.exists(story_dir):
        for fname in sorted(os.listdir(story_dir)):
            if fname.endswith('.json'):
                with open(os.path.join(story_dir, fname), 'r') as f:
                    entries.append(json.load(f))
    return {"entries": entries}

# ============== Chat/DM Logic ==============

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

Always stay in character as the DM. Use second person ("you", "your") when addressing players. Use dice roll notation like [[1d20+5]] or [[2d6+3]] when asking for rolls.

Current context will be provided below."""

@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    char_path = f"./data/characters/{request.character_id}.json"
    if not os.path.exists(char_path):
        raise HTTPException(status_code=404, detail="Character not found")
    with open(char_path, 'r') as f:
        character = json.load(f)

    char_context = await state.query_rag("characters", request.message, n_results=2)
    story_context = await state.query_rag("storyline", request.message, n_results=3)

    context_text = "\n\nCHARACTER INFO:\n"
    for ctx in char_context:
        context_text += f"- {ctx['text']}\n"
    context_text += "\n\nSTORYLINE CONTEXT:\n"
    for ctx in story_context:
        context_text += f"- {ctx['text']} (relevance: {1-ctx['distance']:.2f})\n"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + context_text},
        {"role": "user", "content": f"I am playing as {character['name']}, a level {character['level']} {character['race']} {character['class_type']}. {request.message}"}
    ]

    session_id = request.session_id or f"session_{request.character_id}"
    if session_id in state.sessions:
        for msg in state.sessions[session_id][-5:]:
            messages.append({"role": msg.role, "content": msg.content})

    response_text = await llm_response(messages, temperature=0.8)

    dice_pattern = r'\[\[(\d+d\d+(?:[+-]\d+)?)\]\]'
    dice_rolls = []
    for match in re.finditer(dice_pattern, response_text):
        dice_rolls.append({"notation": match.group(1), "position": match.start()})
    clean_response = re.sub(dice_pattern, r'\1', response_text)

    if session_id not in state.sessions:
        state.sessions[session_id] = []
    state.sessions[session_id].append(Message(role="user", content=request.message, timestamp=datetime.now().isoformat()))
    state.sessions[session_id].append(Message(role="assistant", content=clean_response, timestamp=datetime.now().isoformat()))

    return ChatResponse(
        response=clean_response,
        dice_rolls=dice_rolls,
        context_used=[ctx['text'][:100] + "..." for ctx in char_context + story_context]
    )

# ============== WebSocket for Real-time ==============

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
                    "notation": data.get("notation")
                })
            elif data.get("type") == "hp_update":
                char_id = data.get("character_id")
                new_hp = data.get("hp")
                char_path = f"./data/characters/{char_id}.json"
                if os.path.exists(char_path):
                    with open(char_path, 'r') as f:
                        char = json.load(f)
                    char["hp"] = new_hp
                    char["updated_at"] = datetime.now().isoformat()
                    with open(char_path, 'w') as f:
                        json.dump(char, f, indent=2)
                    await websocket.send_json({"type": "hp_updated", "hp": new_hp})
    except WebSocketDisconnect:
        del state.active_connections[session_id]

# ============== Frontend ==============

@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("static/index.html")

@app.get("/app", response_class=HTMLResponse)
async def app_page():
    return FileResponse("static/app.html")

# ============== Main ==============

if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════╗
    ║           DM-I Starting...            ║
    ║     Your AI Dungeon Master awaits     ║
    ╚═══════════════════════════════════════╝
    
    Make sure llama.cpp servers are running, or use
    the LLM Settings panel to configure a model.
    
    Access DM-I at: http://localhost:8000
    """)
    uvicorn.run(app, host="0.0.0.0", port=8000)