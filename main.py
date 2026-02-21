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
from typing import List, Dict, Optional, Any
from datetime import datetime
from contextlib import asynccontextmanager
import httpx
import chromadb
from chromadb.config import Settings
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
import uvicorn

# Configuration
CONFIG = {
    "glm_server": "http://localhost:8080",  # GLM-4.7-Flash
    "embedding_server": "http://localhost:8081",  # Qwen3-Embedding
    "chroma_path": "./data/chroma_db",
    "max_context": 8192,
    "default_temp": 0.7,
    "default_top_p": 0.9,
}

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

# Global state
class DMState:
    def __init__(self):
        self.chroma_client = None
        self.characters_collection = None
        self.storyline_collection = None
        self.sessions: Dict[str, List[Message]] = {}
        self.active_connections: Dict[str, WebSocket] = {}
        
    async def init_chroma(self):
        """Initialize ChromaDB for RAG"""
        self.chroma_client = chromadb.PersistentClient(
            path=CONFIG["chroma_path"],
            settings=Settings(anonymized_telemetry=False)
        )
        
        # Collection for character sheets
        self.characters_collection = self.chroma_client.get_or_create_collection(
            name="characters",
            metadata={"hnsw:space": "cosine"}
        )
        
        # Collection for storyline/lore
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
            return [0.0] * 768  # Fallback zero embedding
    
    async def add_to_rag(self, collection_name: str, doc_id: str, text: str, metadata: Dict = None):
        """Add document to RAG"""
        embedding = self.get_embedding(text)
        collection = self.characters_collection if collection_name == "characters" else self.storyline_collection
        collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata or {}]
        )
    
    async def query_rag(self, collection_name: str, query: str, n_results: int = 3) -> List[Dict]:
        """Query RAG for relevant context"""
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

# Lifespan management
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    await state.init_chroma()
    yield
    # Cleanup
    print("Shutting down DM-I...")

app = FastAPI(title="DM-I", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# ============== LLM Communication ==============

async def generate_with_glm(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    stream: bool = False
) -> str:
    """Generate text using GLM-4.7-Flash via llama.cpp server"""
    
    # Format for llama.cpp chat completion
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
                f"{CONFIG['glm_server']}/completion",
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
    """Format messages for GLM-4 chat template"""
    formatted = ""
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            formatted += f"<|system|>\n{content}<|end|>\n"
        elif role == "user":
            formatted += f"<|user|>\n{content}<|end|>\n"
        elif role == "assistant":
            formatted += f"<|assistant|>\n{content}<|end|>\n"
    formatted += "<|assistant|>\n"
    return formatted

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
    """Get available D&D classes"""
    return {"classes": DND_CLASSES, "races": DND_RACES}

@app.post("/api/character/create")
async def create_character(
    name: str = Form(...),
    player_name: str = Form(...),
    class_type: str = Form(...),
    race: str = Form(...),
    background: str = Form(...),
    alignment: str = Form(...),
    stats: str = Form("{}"),  # JSON string
    backstory: str = Form("")
):
    """Create new character manually"""
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
    
    # Save to file
    char_path = f"./data/characters/{char_id}.json"
    with open(char_path, 'w') as f:
        f.write(character.model_dump_json(indent=2))
    
    # Add to RAG
    char_text = f"Character: {character.name}\nRace: {character.race}\nClass: {character.class_type} {character.level}\nBackground: {character.background}\nAlignment: {character.alignment}\nBackstory: {character.backstory}\nStats: {character.stats}"
    await state.add_to_rag("characters", char_id, char_text, {"name": character.name, "player": character.player_name})
    
    return {"status": "success", "character": character}

@app.post("/api/character/upload")
async def upload_character_sheet(
    file: UploadFile = File(...),
    player_name: str = Form(...)
):
    """Upload and parse character sheet image using GLM vision"""
    
    # Read image
    contents = await file.read()
    base64_image = base64.b64encode(contents).decode('utf-8')
    
    # Use GLM to parse the character sheet
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
    
    parsed_text = await generate_with_glm(messages, temperature=0.1)
    
    # Try to extract JSON
    try:
        json_match = re.search(r'\{.*\}', parsed_text, re.DOTALL)
        if json_match:
            char_data = json.loads(json_match.group())
            char_data["player_name"] = player_name
            char_data["id"] = f"char_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{char_data.get('name', 'unknown').lower().replace(' ', '_')}"
            char_data["created_at"] = datetime.now().isoformat()
            char_data["updated_at"] = datetime.now().isoformat()
            
            character = Character(**char_data)
            
            # Save
            char_path = f"./data/characters/{character.id}.json"
            with open(char_path, 'w') as f:
                f.write(character.model_dump_json(indent=2))
            
            # Add to RAG
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
    """Get character by ID"""
    char_path = f"./data/characters/{character_id}.json"
    if not os.path.exists(char_path):
        raise HTTPException(status_code=404, detail="Character not found")
    
    with open(char_path, 'r') as f:
        return json.load(f)

@app.get("/api/characters")
async def list_characters():
    """List all characters"""
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
    category: str = Form("lore")  # lore, npc, location, quest, event
):
    """Add storyline element to RAG"""
    story_id = f"story_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{title.lower().replace(' ', '_')[:20]}"
    
    # Add to RAG
    await state.add_to_rag(
        "storyline", 
        story_id, 
        f"{title}\n\n{content}",
        {"title": title, "category": category, "created": datetime.now().isoformat()}
    )
    
    # Save to file
    story_path = f"./data/storyline/{story_id}.json"
    with open(story_path, 'w') as f:
        json.dump({"id": story_id, "title": title, "content": content, "category": category}, f, indent=2)
    
    return {"status": "success", "story_id": story_id}

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
    """Main chat endpoint"""
    
    # Get character info
    char_path = f"./data/characters/{request.character_id}.json"
    if not os.path.exists(char_path):
        raise HTTPException(status_code=404, detail="Character not found")
    
    with open(char_path, 'r') as f:
        character = json.load(f)
    
    # Get relevant context from RAG
    char_context = await state.query_rag("characters", request.message, n_results=2)
    story_context = await state.query_rag("storyline", request.message, n_results=3)
    
    context_text = "\n\nCHARACTER INFO:\n"
    for ctx in char_context:
        context_text += f"- {ctx['text']}\n"
    
    context_text += "\n\nSTORYLINE CONTEXT:\n"
    for ctx in story_context:
        context_text += f"- {ctx['text']} (relevance: {1-ctx['distance']:.2f})\n"
    
    # Build messages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + context_text},
        {"role": "user", "content": f"I am playing as {character['name']}, a level {character['level']} {character['race']} {character['class_type']}. {request.message}"}
    ]
    
    # Add session history if available
    session_id = request.session_id or f"session_{request.character_id}"
    if session_id in state.sessions:
        for msg in state.sessions[session_id][-5:]:
            messages.append({"role": msg.role, "content": msg.content})
    
    # Generate response
    response_text = await generate_with_glm(messages, temperature=0.8)
    
    # Extract dice rolls requested
    dice_pattern = r'\[\[(\d+d\d+(?:[+-]\d+)?)\]\]'
    dice_rolls = []
    for match in re.finditer(dice_pattern, response_text):
        dice_rolls.append({
            "notation": match.group(1),
            "position": match.start()
        })
    
    clean_response = re.sub(dice_pattern, r'\1', response_text)
    
    # Store in session
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
    """WebSocket for real-time updates"""
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
    """Serve main page"""
    return FileResponse("static/index.html")

@app.get("/app", response_class=HTMLResponse)
async def app_page():
    """Serve app page"""
    return FileResponse("static/app.html")

# ============== Main ==============

if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════╗
    ║           DM-I Starting...            ║
    ║     Your AI Dungeon Master awaits     ║
    ╚═══════════════════════════════════════╝
    
    Make sure llama.cpp servers are running:
    
    1. GLM-4.7-Flash (Port 8080):
       ./llama-server -m GLM-4.7-Flash-MXFP4_MOE.gguf \
         --host 0.0.0.0 --port 8080 -c 8192 --chat-template glm4
    
    2. Qwen3-Embedding (Port 8081):
       ./llama-server -m Qwen3-Embedding-0.6B-Q8_0.gguf \
         --host 0.0.0.0 --port 8081 --embedding
    
    Then access: http://localhost:8000
    """)
    
    uvicorn.run(app, host="0.0.0.0", port=8000)