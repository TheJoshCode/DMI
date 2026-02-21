/**
 * DM-I App JavaScript
 * Handles chat, dice rolling, character sheet, lore management, and LLM settings.
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
    initLLMSettings();
});

// ============================================================
// Character init
// ============================================================
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
    document.getElementById('sidebar-char-class').textContent =
        `Level ${currentCharacter.level} ${currentCharacter.race} ${currentCharacter.class_type}`;
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

// ============================================================
// Navigation
// ============================================================
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

// ============================================================
// Chat
// ============================================================
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
        const spells = currentCharacter.spells?.length ? currentCharacter.spells : ['Magic Missile', 'Fireball', 'Cure Wounds'];
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
        const skills = currentCharacter.skills?.length ? currentCharacter.skills : ['Perception', 'Stealth', 'Persuasion', 'Athletics'];
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
                message,
                session_id: sessionId
            })
        });
        const result = await response.json();
        hideTyping();
        addMessage('dm', result.response);

        if (result.dice_rolls?.length > 0) {
            result.dice_rolls.forEach(roll => {
                setTimeout(() => {
                    const rollResult = rollDice(roll.notation);
                    addMessage('system', `Rolled ${roll.notation}: **${rollResult.total}** (${rollResult.rolls.join(', ')})`);
                }, 500);
            });
        }
    } catch (error) {
        hideTyping();
        addMessage('system', 'Error connecting to DM. Please check the LLM status (⚙️ LLM Settings).');
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
        <div class="message-content"><p>${content}</p></div>
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
    typing.innerHTML = `<div class="message-content"><p><em>DM is thinking...</em></p></div>`;
    container.appendChild(typing);
    container.scrollTop = container.scrollHeight;
}

function hideTyping() {
    const typing = document.getElementById('typing-indicator');
    if (typing) typing.remove();
}

// ============================================================
// Dice Roller
// ============================================================
function initDiceRoller() {
    const diceBtns = document.querySelectorAll('.dice-btn');
    const customRoll = document.getElementById('custom-roll');
    const rollCustom = document.getElementById('roll-custom');

    diceBtns.forEach(btn => {
        btn.addEventListener('click', () => performRoll(btn.dataset.roll));
    });
    rollCustom.addEventListener('click', () => {
        if (customRoll.value) performRoll(customRoll.value);
    });
    customRoll.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') rollCustom.click();
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
            ws.send(JSON.stringify({ type: 'roll', notation, result }));
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

// ============================================================
// Character Sheet
// ============================================================
function populateCharacterSheet() {
    document.getElementById('sheet-name').textContent = currentCharacter.name;
    document.getElementById('sheet-details').textContent =
        `${currentCharacter.race} ${currentCharacter.class_type} Level ${currentCharacter.level}`;
    document.getElementById('info-background').textContent = currentCharacter.background || '-';
    document.getElementById('info-alignment').textContent = currentCharacter.alignment || '-';
    document.getElementById('info-player').textContent = currentCharacter.player_name;

    const stats = currentCharacter.stats || {};
    const abilities = ['str', 'dex', 'con', 'int', 'wis', 'cha'];
    const abilityNames = {
        str: 'Strength', dex: 'Dexterity', con: 'Constitution',
        int: 'Intelligence', wis: 'Wisdom', cha: 'Charisma'
    };
    const container = document.getElementById('ability-scores');
    container.innerHTML = abilities.map(ability => {
        const score = stats[ability] || 10;
        const mod = Math.floor((score - 10) / 2);
        const modStr = mod >= 0 ? `+${mod}` : `${mod}`;
        return `
            <div class="ability-item">
                <div class="ability-name">${abilityNames[ability]}</div>
                <div class="ability-score">${score}</div>
                <div class="ability-mod">${modStr}</div>
            </div>`;
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

// ============================================================
// Lore
// ============================================================
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
                await fetch('/api/storyline/add', { method: 'POST', body: formData });
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

    document.getElementById('lore-search').addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase();
        document.querySelectorAll('.lore-entry').forEach(el => {
            el.style.display = el.textContent.toLowerCase().includes(q) ? '' : 'none';
        });
    });
}

async function loadLore() {
    try {
        const res = await fetch('/api/storyline');
        const data = await res.json();
        const container = document.getElementById('lore-entries');
        if (!data.entries || data.entries.length === 0) {
            container.innerHTML = '<p class="empty-state">No lore entries yet. Add some world building!</p>';
            return;
        }
        container.innerHTML = data.entries.map(e => `
            <div class="lore-entry">
                <h4>${e.title}</h4>
                <span class="category">${e.category}</span>
                <p>${e.content}</p>
            </div>
        `).join('');
    } catch (error) {
        console.error('Failed to load lore:', error);
    }
}

// ============================================================
// WebSocket
// ============================================================
function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/${sessionId}`);
    ws.onopen = () => console.log('WebSocket connected');
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'hp_updated') updateHP(data.hp, currentCharacter.max_hp);
    };
    ws.onclose = () => {
        console.log('WebSocket disconnected');
        setTimeout(initWebSocket, 5000);
    };
}

// ============================================================
// Generic Modal helpers
// ============================================================
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

// ============================================================
// LLM Settings Panel
// ============================================================

let llmConfig = null;
let dlPollTimer = null;

function initLLMSettings() {
    document.getElementById('llm-settings-btn').addEventListener('click', openLLMModal);
    document.querySelector('.llm-modal-close')?.addEventListener('click', closeLLMModal);
    document.getElementById('llm-modal-overlay')?.addEventListener('click', (e) => {
        if (e.target.id === 'llm-modal-overlay') closeLLMModal();
    });

    // Tabs
    document.querySelectorAll('.llm-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.llm-tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.llm-tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`ltab-${btn.dataset.ltab}`).classList.add('active');
        });
    });

    // URL → auto-fill filename
    document.getElementById('dl-url').addEventListener('input', (e) => {
        const url = e.target.value.trim();
        const fnameEl = document.getElementById('dl-filename');
        if (!fnameEl.dataset.userEdited) {
            let guessed = url.split('/').pop().split('?')[0];
            if (!guessed.toLowerCase().endsWith('.gguf')) guessed += '.gguf';
            fnameEl.value = guessed;
        }
    });
    document.getElementById('dl-filename').addEventListener('input', (e) => {
        e.target.dataset.userEdited = e.target.value ? '1' : '';
    });

    document.getElementById('dl-start-btn').addEventListener('click', startDownload);
    document.getElementById('llm-apply-btn').addEventListener('click', applyLLMConfig);
    document.getElementById('adv-save-btn').addEventListener('click', saveAdvancedPort);
    document.getElementById('adv-refresh-btn').addEventListener('click', () => {
        loadLocalModels();
        checkLLMStatus();
    });

    // Kick off background status polling
    checkLLMStatus();
    setInterval(checkLLMStatus, 15000);
}

async function openLLMModal() {
    document.getElementById('llm-modal-overlay').classList.remove('hidden');
    await loadLLMConfig();
    await loadLocalModels();
    await checkLLMStatus();
}
function closeLLMModal() {
    document.getElementById('llm-modal-overlay').classList.add('hidden');
    clearInterval(dlPollTimer);
    dlPollTimer = null;
}

async function loadLLMConfig() {
    try {
        const res = await fetch('/api/llm/config');
        llmConfig = await res.json();

        // Populate template dropdowns
        const templates = llmConfig.chat_templates || [];
        ['llm-template-local', 'dl-template'].forEach(id => {
            const sel = document.getElementById(id);
            sel.innerHTML = templates.map(t =>
                `<option value="${t}" ${t === llmConfig.config.chat_template ? 'selected' : ''}>${t}</option>`
            ).join('');
        });

        document.getElementById('llm-ctx-local').value = llmConfig.config.context_size || 8192;
        document.getElementById('llm-gpu-local').value = llmConfig.config.gpu_layers ?? 0;
        document.getElementById('adv-port').value = llmConfig.config.server_port || 8080;
        document.getElementById('adv-url').value = llmConfig.server_url || 'http://localhost:8080';
    } catch (e) {
        console.error('Failed to load LLM config', e);
    }
}

async function loadLocalModels() {
    const container = document.getElementById('llm-model-list');
    container.innerHTML = '<p class="empty-state">Scanning…</p>';
    try {
        const res = await fetch('/api/llm/models');
        const data = await res.json();
        if (!data.models.length) {
            container.innerHTML = `<p class="empty-state">No .gguf files found in <code>${data.models_dir}</code>.<br>Download one below or copy a model file there.</p>`;
            return;
        }
        container.innerHTML = data.models.map(m => `
            <div class="llm-model-row ${m.active ? 'active' : ''}" data-path="${m.path}">
                <div class="llm-model-info">
                    <span class="llm-model-name">${m.filename}</span>
                    <span class="llm-model-size">${m.size_gb} GB</span>
                </div>
                ${m.active
                    ? '<span class="llm-active-badge">✓ Active</span>'
                    : `<button class="btn-secondary llm-select-btn" data-path="${m.path}">Use This Model</button>`
                }
            </div>
        `).join('');

        container.querySelectorAll('.llm-select-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                // Set the path then immediately apply
                document.getElementById('llm-apply-btn').dataset.selectedPath = btn.dataset.path;
                applyLLMConfig();
            });
        });
    } catch (e) {
        container.innerHTML = '<p class="empty-state">Error loading models.</p>';
    }
}

async function applyLLMConfig() {
    const applyBtn = document.getElementById('llm-apply-btn');

    const modelPath = applyBtn.dataset.selectedPath
        || document.querySelector('.llm-model-row.active')?.dataset.path
        || llmConfig?.config?.model_path;

    if (!modelPath) {
        showApplyStatus('⚠ Please select a model first.', 'warn');
        return;
    }

    // Visually mark the target row as loading
    document.querySelectorAll('.llm-model-row').forEach(r => r.classList.remove('selected'));
    const targetRow = document.querySelector(`.llm-model-row[data-path="${modelPath}"]`);
    if (targetRow) {
        targetRow.classList.add('selected');
        const btn = targetRow.querySelector('.llm-select-btn');
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Switching…'; }
    }

    const template = document.getElementById('llm-template-local').value;
    const ctx = parseInt(document.getElementById('llm-ctx-local').value);
    const gpu = parseInt(document.getElementById('llm-gpu-local').value);
    const port = parseInt(document.getElementById('adv-port').value) || 8080;

    applyBtn.disabled = true;
    applyBtn.textContent = '⏳ Restarting…';
    showApplyStatus('Sending config to backend…', 'info');

    try {
        const res = await fetch('/api/llm/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model_path: modelPath,
                chat_template: template,
                context_size: ctx,
                gpu_layers: gpu,
                server_port: port
            })
        });
        const result = await res.json();
        if (res.ok) {
            showApplyStatus(`✅ LLM switched to <strong>${modelPath.split('/').pop()}</strong>. Waiting for server…`, 'success');
            setTimeout(async () => {
                await checkLLMStatus();
                await loadLocalModels();
            }, 5000);
        } else {
            showApplyStatus(`❌ Error: ${result.detail || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        showApplyStatus(`❌ Request failed: ${e.message}`, 'error');
    } finally {
        applyBtn.disabled = false;
        applyBtn.textContent = '⚡ Apply & Restart LLM';
    }
}

function showApplyStatus(html, type) {
    const el = document.getElementById('llm-apply-status');
    el.innerHTML = html;
    el.className = `llm-apply-status llm-status-${type}`;
    el.classList.remove('hidden');
}

async function saveAdvancedPort() {
    const port = parseInt(document.getElementById('adv-port').value) || 8080;
    document.getElementById('adv-url').value = `http://localhost:${port}`;
    // This will be picked up when the user clicks Apply in Local Models tab
    document.getElementById('adv-save-btn').textContent = '✓ Saved (apply in Local Models)';
    setTimeout(() => { document.getElementById('adv-save-btn').textContent = 'Save Port'; }, 2000);
}

async function startDownload() {
    const url = document.getElementById('dl-url').value.trim();
    const filename = document.getElementById('dl-filename').value.trim();
    const template = document.getElementById('dl-template').value;
    const autoSwitch = document.getElementById('dl-autoswitch').checked;

    if (!url) {
        alert('Please enter a download URL.');
        return;
    }

    const btn = document.getElementById('dl-start-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Starting…';

    try {
        const res = await fetch('/api/llm/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, filename: filename || undefined, auto_switch: autoSwitch, chat_template: template })
        });
        const data = await res.json();
        if (res.ok && data.status !== 'error') {
            document.getElementById('dl-url').value = '';
            document.getElementById('dl-filename').value = '';
            document.getElementById('dl-filename').dataset.userEdited = '';
            startDownloadPolling();
        } else {
            alert(`Download failed: ${data.detail || data.status}`);
        }
    } catch (e) {
        alert(`Request error: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = '⬇️ Start Download';
    }
}

function startDownloadPolling() {
    if (dlPollTimer) return;
    dlPollTimer = setInterval(pollDownloads, 1500);
    pollDownloads();
}

async function pollDownloads() {
    try {
        const res = await fetch('/api/llm/download/status');
        const data = await res.json();
        renderDownloads(data.downloads);

        // Stop polling if all done
        const active = Object.values(data.downloads).some(d => d.status === 'downloading' || d.status === 'starting');
        if (!active && dlPollTimer) {
            clearInterval(dlPollTimer);
            dlPollTimer = null;
            await loadLocalModels(); // refresh after download completes
        }
    } catch (e) {
        console.warn('Download poll error', e);
    }
}

function renderDownloads(downloads) {
    const container = document.getElementById('dl-progress-list');
    const entries = Object.entries(downloads);
    if (!entries.length) { container.innerHTML = ''; return; }

    container.innerHTML = entries.map(([fname, info]) => {
        const pct = info.percent || 0;
        const mbDL = (info.downloaded / 1024 / 1024).toFixed(1);
        const mbTotal = info.total ? (info.total / 1024 / 1024).toFixed(1) : '?';
        const statusIcon = info.status === 'complete' ? '✅' : info.status === 'error' ? '❌' : '⬇️';
        return `
            <div class="dl-item">
                <div class="dl-item-header">
                    <span class="dl-fname">${statusIcon} ${fname}</span>
                    <span class="dl-pct">${pct}%</span>
                </div>
                <div class="dl-bar-wrap">
                    <div class="dl-bar" style="width:${pct}%"></div>
                </div>
                <div class="dl-meta">${mbDL} MB / ${mbTotal} MB — <em>${info.status}</em>${info.error ? ` — ${info.error}` : ''}</div>
            </div>
        `;
    }).join('');
}

async function checkLLMStatus() {
    try {
        const res = await fetch('/api/llm/status');
        const data = await res.json();
        setLLMDotStatus(data.online ? 'online' : 'offline');
        const label = document.getElementById('llm-model-label');
        if (label) label.textContent = data.online ? `🤖 ${data.model}` : '';

        const modalText = document.getElementById('llm-status-text');
        const modalModel = document.getElementById('llm-active-model');
        const modalDot = document.getElementById('llm-modal-dot');
        if (modalText) modalText.textContent = data.online ? 'LLM server online' : 'LLM server offline';
        if (modalModel) modalModel.textContent = data.online ? data.model : '';
        if (modalDot) {
            modalDot.className = `llm-dot llm-dot--${data.online ? 'online' : 'offline'}`;
        }
    } catch {
        setLLMDotStatus('offline');
    }
}

function setLLMDotStatus(status) {
    const dot = document.getElementById('llm-dot');
    if (dot) dot.className = `llm-dot llm-dot--${status}`;
}