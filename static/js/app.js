/**
 * DM-I App JavaScript
 * Handles chat, dice rolling, character sheet, and lore management
 */

let currentCharacter = null;
let sessionId = null;
let ws = null;
let rollHistory = [];

document.addEventListener('DOMContentLoaded', () => {
    initCharacter();
    initNavigation();
    initChat();
    initDiceRoller();
    initCharacterSheet();
    initLore();
    initWebSocket();
});

function initCharacter() {
    const stored = localStorage.getItem('currentCharacter');
    if (!stored) {
        window.location.href = '/';
        return;
    }
    
    currentCharacter = JSON.parse(stored);
    sessionId = `session_${currentCharacter.id}_${Date.now()}`;
    
    document.getElementById('char-initials').textContent = currentCharacter.name.substring(0, 2).toUpperCase();
    document.getElementById('sidebar-char-name').textContent = currentCharacter.name;
    document.getElementById('sidebar-char-class').textContent = `Level ${currentCharacter.level} ${currentCharacter.race} ${currentCharacter.class_type}`;
    document.getElementById('session-id').textContent = `Session: ${sessionId.slice(-8)}`;
    
    updateHP(currentCharacter.hp, currentCharacter.max_hp);
    document.getElementById('quick-ac').textContent = currentCharacter.ac || 10;
    document.getElementById('quick-level').textContent = currentCharacter.level;
    
    populateCharacterSheet();
}

function updateHP(current, max) {
    const fill = document.getElementById('hp-fill');
    const text = document.getElementById('hp-text');
    const percentage = (current / max) * 100;
    
    fill.style.width = `${percentage}%`;
    text.textContent = `HP: ${current}/${max}`;
    
    fill.className = 'hp-fill';
    if (percentage > 60) fill.classList.add('high');
    else if (percentage > 30) fill.classList.add('medium');
    else fill.classList.add('low');
}

function initNavigation() {
    const navBtns = document.querySelectorAll('.nav-btn');
    const views = document.querySelectorAll('.view');
    
    navBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const viewId = btn.dataset.view;
            
            navBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            views.forEach(v => v.classList.remove('active'));
            document.getElementById(`${viewId}-view`).classList.add('active');
        });
    });
    
    document.getElementById('menu-toggle')?.addEventListener('click', () => {
        document.querySelector('.sidebar').classList.toggle('open');
    });
    
    document.getElementById('new-game').addEventListener('click', () => {
        localStorage.removeItem('currentCharacter');
        window.location.href = '/';
    });
}

function initChat() {
    const input = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    
    sendBtn.addEventListener('click', sendMessage);
    
    document.getElementById('action-attack').addEventListener('click', () => {
        input.value = 'I attack with my weapon';
        input.focus();
    });
    
    document.getElementById('action-check').addEventListener('click', () => {
        showModal('Ability Check', `
            <div class="check-options">
                <button class="check-btn" data-check="strength">Strength</button>
                <button class="check-btn" data-check="dexterity">Dexterity</button>
                <button class="check-btn" data-check="constitution">Constitution</button>
                <button class="check-btn" data-check="intelligence">Intelligence</button>
                <button class="check-btn" data-check="wisdom">Wisdom</button>
                <button class="check-btn" data-check="charisma">Charisma</button>
            </div>
        `);
        
        document.querySelectorAll('.check-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                input.value = `I make a ${btn.dataset.check} check`;
                closeModal();
                input.focus();
            });
        });
    });
    
    document.getElementById('action-spell').addEventListener('click', () => {
        const spells = currentCharacter.spells || ['Magic Missile', 'Fireball', 'Cure Wounds'];
        const spellList = spells.map(s => `<button class="spell-btn">${s}</button>`).join('');
        showModal('Cast Spell', `<div class="spell-list">${spellList}</div>`);
        
        document.querySelectorAll('.spell-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                input.value = `I cast ${btn.textContent}`;
                closeModal();
                input.focus();
            });
        });
    });
    
    document.getElementById('action-skill').addEventListener('click', () => {
        const skills = currentCharacter.skills || ['Perception', 'Stealth', 'Persuasion', 'Athletics'];
        const skillList = skills.map(s => `<button class="skill-btn">${s}</button>`).join('');
        showModal('Use Skill', `<div class="skill-list">${skillList}</div>`);
        
        document.querySelectorAll('.skill-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                input.value = `I use ${btn.textContent}`;
                closeModal();
                input.focus();
            });
        });
    });
}

async function sendMessage() {
    const input = document.getElementById('message-input');
    const message = input.value.trim();
    if (!message) return;
    
    addMessage('user', message);
    input.value = '';
    
    showTyping();
    
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                character_id: currentCharacter.id,
                message: message,
                session_id: sessionId
            })
        });
        
        const result = await response.json();
        hideTyping();
        addMessage('dm', result.response);
        
        if (result.dice_rolls.length > 0) {
            result.dice_rolls.forEach(roll => {
                setTimeout(() => {
                    const rollResult = rollDice(roll.notation);
                    addMessage('system', `Rolled ${roll.notation}: **${rollResult.total}** (${rollResult.rolls.join(', ')})`);
                }, 500);
            });
        }
    } catch (error) {
        hideTyping();
        addMessage('system', 'Error connecting to DM. Please try again.');
    }
}

function addMessage(role, content) {
    const container = document.getElementById('chat-messages');
    const message = document.createElement('div');
    message.className = `message ${role}`;
    
    const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    
    content = content
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/\n/g, '<br>');
    
    message.innerHTML = `
        <div class="message-content">
            <p>${content}</p>
        </div>
        <span class="timestamp">${timestamp}</span>
    `;
    
    container.appendChild(message);
    container.scrollTop = container.scrollHeight;
}

function showTyping() {
    const container = document.getElementById('chat-messages');
    const typing = document.createElement('div');
    typing.id = 'typing-indicator';
    typing.className = 'message dm';
    typing.innerHTML = `
        <div class="message-content">
            <p><em>DM is thinking...</em></p>
        </div>
    `;
    container.appendChild(typing);
    container.scrollTop = container.scrollHeight;
}

function hideTyping() {
    const typing = document.getElementById('typing-indicator');
    if (typing) typing.remove();
}

function initDiceRoller() {
    const diceBtns = document.querySelectorAll('.dice-btn');
    const customRoll = document.getElementById('custom-roll');
    const rollCustom = document.getElementById('roll-custom');
    
    diceBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const notation = btn.dataset.roll;
            performRoll(notation);
        });
    });
    
    rollCustom.addEventListener('click', () => {
        const notation = customRoll.value;
        if (notation) performRoll(notation);
    });
    
    customRoll.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            rollCustom.click();
        }
    });
}

function performRoll(notation) {
    const resultEl = document.getElementById('dice-result');
    const breakdownEl = document.getElementById('dice-breakdown');
    
    resultEl.classList.add('rolling');
    resultEl.textContent = '...';
    
    setTimeout(() => {
        const result = rollDice(notation);
        resultEl.classList.remove('rolling');
        resultEl.textContent = result.total;
        breakdownEl.textContent = `${notation}: [${result.rolls.join(', ')}]${result.modifier ? ' + ' + result.modifier : ''} = ${result.total}`;
        
        addToRollHistory(notation, result);
        
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'roll',
                notation: notation,
                result: result
            }));
        }
    }, 500);
}

function rollDice(notation) {
    const match = notation.match(/(\d+)d(\d+)(?:([+-])(\d+))?/);
    if (!match) return { total: 0, rolls: [], modifier: 0 };
    
    const numDice = parseInt(match[1]);
    const numSides = parseInt(match[2]);
    const modifier = match[3] ? parseInt(match[3] + match[4]) : 0;
    
    const rolls = [];
    for (let i = 0; i < numDice; i++) {
        rolls.push(Math.floor(Math.random() * numSides) + 1);
    }
    
    const total = rolls.reduce((a, b) => a + b, 0) + modifier;
    return { total, rolls, modifier };
}

function addToRollHistory(notation, result) {
    rollHistory.unshift({ notation, result, time: new Date() });
    if (rollHistory.length > 10) rollHistory.pop();
    
    const list = document.getElementById('roll-history-list');
    list.innerHTML = rollHistory.map(r => `
        <li>
            <span>${r.notation}</span>
            <span class="roll-total">${r.result.total}</span>
        </li>
    `).join('');
}

function populateCharacterSheet() {
    document.getElementById('sheet-name').textContent = currentCharacter.name;
    document.getElementById('sheet-details').textContent = `${currentCharacter.race} ${currentCharacter.class_type} Level ${currentCharacter.level}`;
    document.getElementById('info-background').textContent = currentCharacter.background || '-';
    document.getElementById('info-alignment').textContent = currentCharacter.alignment || '-';
    document.getElementById('info-player').textContent = currentCharacter.player_name;
    
    const stats = currentCharacter.stats || {};
    const abilities = ['str', 'dex', 'con', 'int', 'wis', 'cha'];
    const abilityNames = { str: 'Strength', dex: 'Dexterity', con: 'Constitution', int: 'Intelligence', wis: 'Wisdom', cha: 'Charisma' };
    
    const container = document.getElementById('ability-scores');
    container.innerHTML = abilities.map(ability => {
        const score = stats[ability] || 10;
        const mod = Math.floor((score - 10) / 2);
        const modStr = mod >= 0 ? `+${mod}` : mod;
        return `
            <div class="ability-item">
                <div class="ability-name">${abilityNames[ability]}</div>
                <div class="ability-score">${score}</div>
                <div class="ability-mod">${modStr}</div>
            </div>
        `;
    }).join('');
    
    document.getElementById('edit-hp').value = currentCharacter.hp || 10;
    document.getElementById('edit-max-hp').value = currentCharacter.max_hp || 10;
    document.getElementById('edit-ac').value = currentCharacter.ac || 10;
}

function initCharacterSheet() {
    document.getElementById('save-character').addEventListener('click', async () => {
        const hp = parseInt(document.getElementById('edit-hp').value);
        const maxHp = parseInt(document.getElementById('edit-max-hp').value);
        const ac = parseInt(document.getElementById('edit-ac').value);
        
        currentCharacter.hp = hp;
        currentCharacter.max_hp = maxHp;
        currentCharacter.ac = ac;
        currentCharacter.updated_at = new Date().toISOString();
        
        updateHP(hp, maxHp);
        document.getElementById('quick-ac').textContent = ac;
        
        try {
            await fetch(`/api/character/${currentCharacter.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentCharacter)
            });
            
            localStorage.setItem('currentCharacter', JSON.stringify(currentCharacter));
            alert('Character saved!');
        } catch (error) {
            console.error('Failed to save:', error);
        }
    });
    
    document.getElementById('export-character').addEventListener('click', () => {
        const dataStr = JSON.stringify(currentCharacter, null, 2);
        const blob = new Blob([dataStr], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${currentCharacter.name.replace(/\s+/g, '_')}.json`;
        a.click();
    });
}

function initLore() {
    loadLore();
    
    document.getElementById('add-lore').addEventListener('click', () => {
        showModal('Add Lore Entry', `
            <form id="lore-form">
                <div class="form-group">
                    <label>Title</label>
                    <input type="text" id="lore-title" required>
                </div>
                <div class="form-group">
                    <label>Category</label>
                    <select id="lore-category">
                        <option value="lore">Lore</option>
                        <option value="npc">NPC</option>
                        <option value="location">Location</option>
                        <option value="quest">Quest</option>
                        <option value="event">Event</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Content</label>
                    <textarea id="lore-content" rows="5" required></textarea>
                </div>
                <button type="submit" class="btn-primary">Add Entry</button>
            </form>
        `);
        
        document.getElementById('lore-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const formData = new FormData();
            formData.append('title', document.getElementById('lore-title').value);
            formData.append('category', document.getElementById('lore-category').value);
            formData.append('content', document.getElementById('lore-content').value);
            
            try {
                await fetch('/api/storyline/add', {
                    method: 'POST',
                    body: formData
                });
                closeModal();
                loadLore();
            } catch (error) {
                console.error('Failed to add lore:', error);
            }
        });
    });
    
    document.getElementById('save-story').addEventListener('click', () => {
        document.getElementById('add-lore').click();
    });
}

async function loadLore() {
    try {
        const container = document.getElementById('lore-entries');
        container.innerHTML = '<p class="empty-state">Lore entries will appear here...</p>';
    } catch (error) {
        console.error('Failed to load lore:', error);
    }
}

function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/${sessionId}`);
    
    ws.onopen = () => {
        console.log('WebSocket connected');
    };
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'hp_updated') {
            updateHP(data.hp, currentCharacter.max_hp);
        }
    };
    
    ws.onclose = () => {
        console.log('WebSocket disconnected');
        setTimeout(initWebSocket, 5000);
    };
}

function showModal(title, content) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = content;
    document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.add('hidden');
}

document.querySelector('.modal-close')?.addEventListener('click', closeModal);
document.getElementById('modal-overlay')?.addEventListener('click', (e) => {
    if (e.target.id === 'modal-overlay') closeModal();
});