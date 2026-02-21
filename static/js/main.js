/**
 * DM-I Main JavaScript
 * Handles character creation, upload, session management, and LLM settings.
 */

let currentCharacter = null;
let classes = [];
let races = [];
let llmConfig = null;
let dlPollTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadClassesAndRaces();
    initFormHandlers();
    initUploadHandlers();
    loadExistingCharacters();
    initStatRoller();
    initLLMSettingsIndex();
});

// ============================================================
// Tab navigation
// ============================================================
function initTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content:not(.llm-tab-content)');
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.dataset.tab;
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            tabContents.forEach(c => c.classList.remove('active'));
            document.getElementById(`${tabId}-tab`).classList.add('active');
        });
    });
}

// ============================================================
// Classes / Races
// ============================================================
async function loadClassesAndRaces() {
    try {
        const response = await fetch('/api/classes');
        const data = await response.json();
        classes = data.classes;
        races = data.races;
        const classSelect = document.getElementById('char-class');
        const raceSelect = document.getElementById('char-race');
        classes.forEach(cls => {
            const option = document.createElement('option');
            option.value = cls; option.textContent = cls;
            classSelect.appendChild(option);
        });
        races.forEach(race => {
            const option = document.createElement('option');
            option.value = race; option.textContent = race;
            raceSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Failed to load classes/races:', error);
        // fallback
        ['Fighter', 'Wizard', 'Rogue', 'Cleric'].forEach(c => {
            const o = document.createElement('option'); o.value = c; o.textContent = c;
            document.getElementById('char-class').appendChild(o);
        });
        ['Human', 'Elf', 'Dwarf', 'Halfling'].forEach(r => {
            const o = document.createElement('option'); o.value = r; o.textContent = r;
            document.getElementById('char-race').appendChild(o);
        });
    }
}

// ============================================================
// Stat roller
// ============================================================
function initStatRoller() {
    const rollBtn = document.getElementById('roll-stats');
    rollBtn.addEventListener('click', () => {
        ['str', 'dex', 'con', 'int', 'wis', 'cha'].forEach(stat => {
            const rolls = Array.from({ length: 4 }, () => Math.floor(Math.random() * 6) + 1);
            rolls.sort((a, b) => b - a);
            document.getElementById(`stat-${stat}`).value = rolls[0] + rolls[1] + rolls[2];
        });
        rollBtn.textContent = 'Stats Rolled!';
        setTimeout(() => { rollBtn.textContent = 'Roll Stats (4d6 drop lowest)'; }, 2000);
    });
}

// ============================================================
// Character creation form
// ============================================================
function initFormHandlers() {
    const form = document.getElementById('create-form');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const stats = {
            str: parseInt(document.getElementById('stat-str').value),
            dex: parseInt(document.getElementById('stat-dex').value),
            con: parseInt(document.getElementById('stat-con').value),
            int: parseInt(document.getElementById('stat-int').value),
            wis: parseInt(document.getElementById('stat-wis').value),
            cha: parseInt(document.getElementById('stat-cha').value)
        };
        const formData = new FormData();
        formData.append('name', document.getElementById('char-name').value);
        formData.append('player_name', document.getElementById('player-name').value);
        formData.append('class_type', document.getElementById('char-class').value);
        formData.append('race', document.getElementById('char-race').value);
        formData.append('background', document.getElementById('char-background').value);
        formData.append('alignment', document.getElementById('char-alignment').value);
        formData.append('stats', JSON.stringify(stats));
        formData.append('backstory', document.getElementById('char-backstory').value);

        try {
            const response = await fetch('/api/character/create', { method: 'POST', body: formData });
            const result = await response.json();
            if (result.status === 'success') {
                currentCharacter = result.character;
                localStorage.setItem('currentCharacter', JSON.stringify(currentCharacter));
                window.location.href = '/app';
            }
        } catch (error) {
            console.error('Failed to create character:', error);
            alert('Failed to create character. Please try again.');
        }
    });
}

// ============================================================
// Upload handler
// ============================================================
function initUploadHandlers() {
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('sheet-upload');
    const preview = document.getElementById('upload-preview');
    const previewImg = document.getElementById('preview-img');

    uploadArea.addEventListener('click', () => fileInput.click());
    uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
    uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault(); uploadArea.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', (e) => { if (e.target.files.length) handleFile(e.target.files[0]); });

    function handleFile(file) {
        if (!file.type.startsWith('image/')) { alert('Please upload an image file'); return; }
        const reader = new FileReader();
        reader.onload = (e) => {
            previewImg.src = e.target.result;
            uploadArea.classList.add('hidden');
            preview.classList.remove('hidden');
        };
        reader.readAsDataURL(file);
    }

    document.getElementById('confirm-upload').addEventListener('click', async () => {
        const playerName = document.getElementById('upload-player-name').value;
        if (!playerName) { alert('Please enter your name'); return; }
        const file = fileInput.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        formData.append('player_name', playerName);
        try {
            const response = await fetch('/api/character/upload', { method: 'POST', body: formData });
            const result = await response.json();
            if (result.status === 'success') {
                currentCharacter = result.character;
                localStorage.setItem('currentCharacter', JSON.stringify(currentCharacter));
                window.location.href = '/app';
            } else if (result.status === 'needs_verification') {
                alert('Character sheet parsed. Please verify the details in the form.');
                document.querySelector('[data-tab="create"]').click();
            }
        } catch (error) { console.error('Upload failed:', error); alert('Failed to upload character sheet'); }
    });
}

// ============================================================
// Load existing characters
// ============================================================
async function loadExistingCharacters() {
    try {
        const response = await fetch('/api/characters');
        const data = await response.json();
        const container = document.getElementById('characters-list');
        if (!data.characters.length) {
            container.innerHTML = '<p class="empty-state">No saved characters found...</p>';
            return;
        }
        container.innerHTML = '';
        data.characters.forEach(char => {
            const card = document.createElement('div');
            card.className = 'character-card';
            card.innerHTML = `
                <div class="character-info">
                    <h4>${char.name}</h4>
                    <p>Level ${char.level} ${char.race} ${char.class_type}</p>
                </div>
                <div class="character-meta">
                    <div>Player: ${char.player_name}</div>
                    <div>${new Date(char.updated_at).toLocaleDateString()}</div>
                </div>
            `;
            card.addEventListener('click', () => {
                localStorage.setItem('currentCharacter', JSON.stringify(char));
                window.location.href = '/app';
            });
            container.appendChild(card);
        });
    } catch (error) { console.error('Failed to load characters:', error); }
}

// ============================================================
// LLM Settings (index page)
// ============================================================
function initLLMSettingsIndex() {
    // Open modal
    document.getElementById('llm-settings-btn-index')?.addEventListener('click', openLLMModalIndex);
    document.querySelector('.llm-modal-close')?.addEventListener('click', closeLLMModalIndex);
    document.getElementById('llm-modal-overlay')?.addEventListener('click', (e) => {
        if (e.target.id === 'llm-modal-overlay') closeLLMModalIndex();
    });

    // LLM sub-tabs
    document.querySelectorAll('.llm-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.llm-tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.llm-tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`ltab-${btn.dataset.ltab}`).classList.add('active');
        });
    });

    // URL → auto-fill filename
    document.getElementById('dl-url')?.addEventListener('input', (e) => {
        const fnameEl = document.getElementById('dl-filename');
        if (!fnameEl.dataset.userEdited) {
            let guessed = e.target.value.trim().split('/').pop().split('?')[0];
            if (!guessed.toLowerCase().endsWith('.gguf')) guessed += '.gguf';
            fnameEl.value = guessed;
        }
    });
    document.getElementById('dl-filename')?.addEventListener('input', (e) => {
        e.target.dataset.userEdited = e.target.value ? '1' : '';
    });

    document.getElementById('dl-start-btn')?.addEventListener('click', startDownloadIndex);
    document.getElementById('llm-apply-btn')?.addEventListener('click', applyLLMConfigIndex);
    document.getElementById('adv-save-btn')?.addEventListener('click', saveAdvancedPortIndex);
    document.getElementById('adv-refresh-btn')?.addEventListener('click', () => {
        loadLocalModelsIndex();
        checkLLMStatusIndex();
    });

    // Background status check
    checkLLMStatusIndex();
    setInterval(checkLLMStatusIndex, 15000);
}

async function openLLMModalIndex() {
    document.getElementById('llm-modal-overlay').classList.remove('hidden');
    await loadLLMConfigIndex();
    await loadLocalModelsIndex();
    await checkLLMStatusIndex();
}
function closeLLMModalIndex() {
    document.getElementById('llm-modal-overlay').classList.add('hidden');
    if (dlPollTimer) { clearInterval(dlPollTimer); dlPollTimer = null; }
}

async function loadLLMConfigIndex() {
    try {
        const res = await fetch('/api/llm/config');
        llmConfig = await res.json();
        const templates = llmConfig.chat_templates || [];
        ['llm-template-local', 'dl-template'].forEach(id => {
            const sel = document.getElementById(id);
            if (!sel) return;
            sel.innerHTML = templates.map(t =>
                `<option value="${t}" ${t === llmConfig.config.chat_template ? 'selected' : ''}>${t}</option>`
            ).join('');
        });
        const ctx = document.getElementById('llm-ctx-local');
        if (ctx) ctx.value = llmConfig.config.context_size || 8192;
        const gpu = document.getElementById('llm-gpu-local');
        if (gpu) gpu.value = llmConfig.config.gpu_layers ?? 0;
        const port = document.getElementById('adv-port');
        if (port) port.value = llmConfig.config.server_port || 8080;
        const url = document.getElementById('adv-url');
        if (url) url.value = llmConfig.server_url || 'http://localhost:8080';
    } catch (e) { console.error('Failed to load LLM config', e); }
}

async function loadLocalModelsIndex() {
    const container = document.getElementById('llm-model-list');
    if (!container) return;
    container.innerHTML = '<p class="empty-state">Scanning…</p>';
    try {
        const res = await fetch('/api/llm/models');
        const data = await res.json();
        if (!data.models.length) {
            container.innerHTML = `<p class="empty-state">No .gguf files in <code>${data.models_dir}</code>.<br>Download one below.</p>`;
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
                document.querySelectorAll('.llm-model-row').forEach(r => r.classList.remove('selected'));
                const row = container.querySelector(`.llm-model-row[data-path="${btn.dataset.path}"]`);
                if (row) row.classList.add('selected');
                document.getElementById('llm-apply-btn').dataset.selectedPath = btn.dataset.path;
            });
        });
    } catch (e) { container.innerHTML = '<p class="empty-state">Error loading models.</p>'; }
}

async function applyLLMConfigIndex() {
    const applyBtn = document.getElementById('llm-apply-btn');
    const modelPath = applyBtn.dataset.selectedPath
        || document.querySelector('.llm-model-row.active')?.dataset.path
        || llmConfig?.config?.model_path;

    if (!modelPath) { showApplyStatusIndex('⚠ Please select a model first.', 'warn'); return; }

    const template = document.getElementById('llm-template-local').value;
    const ctx = parseInt(document.getElementById('llm-ctx-local').value);
    const gpu = parseInt(document.getElementById('llm-gpu-local').value);
    const port = parseInt(document.getElementById('adv-port').value) || 8080;

    applyBtn.disabled = true; applyBtn.textContent = '⏳ Restarting…';
    showApplyStatusIndex('Sending config to backend…', 'info');
    try {
        const res = await fetch('/api/llm/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_path: modelPath, chat_template: template, context_size: ctx, gpu_layers: gpu, server_port: port })
        });
        const result = await res.json();
        if (res.ok) {
            showApplyStatusIndex(`✅ LLM switched to <strong>${modelPath.split('/').pop()}</strong>`, 'success');
            setTimeout(async () => { await checkLLMStatusIndex(); await loadLocalModelsIndex(); }, 5000);
        } else {
            showApplyStatusIndex(`❌ ${result.detail || 'Error'}`, 'error');
        }
    } catch (e) { showApplyStatusIndex(`❌ ${e.message}`, 'error'); }
    finally { applyBtn.disabled = false; applyBtn.textContent = '⚡ Apply & Restart LLM'; }
}

function showApplyStatusIndex(html, type) {
    const el = document.getElementById('llm-apply-status');
    if (!el) return;
    el.innerHTML = html;
    el.className = `llm-apply-status llm-status-${type}`;
    el.classList.remove('hidden');
}

async function saveAdvancedPortIndex() {
    const port = parseInt(document.getElementById('adv-port').value) || 8080;
    document.getElementById('adv-url').value = `http://localhost:${port}`;
    const btn = document.getElementById('adv-save-btn');
    btn.textContent = '✓ Saved'; setTimeout(() => { btn.textContent = 'Save Port'; }, 2000);
}

async function startDownloadIndex() {
    const url = document.getElementById('dl-url').value.trim();
    const filename = document.getElementById('dl-filename').value.trim();
    const template = document.getElementById('dl-template').value;
    const autoSwitch = document.getElementById('dl-autoswitch').checked;

    if (!url) { alert('Please enter a download URL.'); return; }
    const btn = document.getElementById('dl-start-btn');
    btn.disabled = true; btn.textContent = '⏳ Starting…';
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
            startDownloadPollingIndex();
        } else { alert(`Download failed: ${data.detail || data.status}`); }
    } catch (e) { alert(`Request error: ${e.message}`); }
    finally { btn.disabled = false; btn.textContent = '⬇️ Start Download'; }
}

function startDownloadPollingIndex() {
    if (dlPollTimer) return;
    dlPollTimer = setInterval(pollDownloadsIndex, 1500);
    pollDownloadsIndex();
}

async function pollDownloadsIndex() {
    try {
        const res = await fetch('/api/llm/download/status');
        const data = await res.json();
        renderDownloadsIndex(data.downloads);
        const active = Object.values(data.downloads).some(d => d.status === 'downloading' || d.status === 'starting');
        if (!active && dlPollTimer) { clearInterval(dlPollTimer); dlPollTimer = null; await loadLocalModelsIndex(); }
    } catch (e) { console.warn('Poll error', e); }
}

function renderDownloadsIndex(downloads) {
    const container = document.getElementById('dl-progress-list');
    if (!container) return;
    const entries = Object.entries(downloads);
    if (!entries.length) { container.innerHTML = ''; return; }
    container.innerHTML = entries.map(([fname, info]) => {
        const pct = info.percent || 0;
        const mbDL = (info.downloaded / 1024 / 1024).toFixed(1);
        const mbTotal = info.total ? (info.total / 1024 / 1024).toFixed(1) : '?';
        const icon = info.status === 'complete' ? '✅' : info.status === 'error' ? '❌' : '⬇️';
        return `
            <div class="dl-item">
                <div class="dl-item-header">
                    <span class="dl-fname">${icon} ${fname}</span>
                    <span class="dl-pct">${pct}%</span>
                </div>
                <div class="dl-bar-wrap"><div class="dl-bar" style="width:${pct}%"></div></div>
                <div class="dl-meta">${mbDL} MB / ${mbTotal} MB — <em>${info.status}</em>${info.error ? ` — ${info.error}` : ''}</div>
            </div>`;
    }).join('');
}

async function checkLLMStatusIndex() {
    try {
        const res = await fetch('/api/llm/status');
        const data = await res.json();
        const dot = document.getElementById('index-llm-dot');
        const label = document.getElementById('index-llm-label');
        if (dot) dot.className = `llm-dot-sm llm-dot-sm--${data.online ? 'online' : 'offline'}`;
        if (label) label.textContent = data.online ? data.model : 'LLM offline';

        const modalDot = document.getElementById('llm-modal-dot');
        const modalText = document.getElementById('llm-status-text');
        const modalModel = document.getElementById('llm-active-model');
        if (modalDot) modalDot.className = `llm-dot llm-dot--${data.online ? 'online' : 'offline'}`;
        if (modalText) modalText.textContent = data.online ? 'LLM server online' : 'LLM server offline';
        if (modalModel) modalModel.textContent = data.online ? data.model : '';
    } catch { 
        const dot = document.getElementById('index-llm-dot');
        if (dot) dot.className = 'llm-dot-sm llm-dot-sm--offline';
    }
}