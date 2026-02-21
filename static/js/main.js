/**
 * DM-I Main JavaScript
 * Handles character creation, upload, and session management
 */

let currentCharacter = null;
let classes = [];
let races = [];

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadClassesAndRaces();
    initFormHandlers();
    initUploadHandlers();
    loadExistingCharacters();
    initStatRoller();
});

function initTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    
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
            option.value = cls;
            option.textContent = cls;
            classSelect.appendChild(option);
        });
        
        races.forEach(race => {
            const option = document.createElement('option');
            option.value = race;
            option.textContent = race;
            raceSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Failed to load classes/races:', error);
        classes = ['Fighter', 'Wizard', 'Rogue', 'Cleric'];
        races = ['Human', 'Elf', 'Dwarf', 'Halfling'];
    }
}

function initStatRoller() {
    const rollBtn = document.getElementById('roll-stats');
    rollBtn.addEventListener('click', () => {
        const stats = ['str', 'dex', 'con', 'int', 'wis', 'cha'];
        stats.forEach(stat => {
            const rolls = [];
            for (let i = 0; i < 4; i++) {
                rolls.push(Math.floor(Math.random() * 6) + 1);
            }
            rolls.sort((a, b) => b - a);
            const total = rolls[0] + rolls[1] + rolls[2];
            document.getElementById(`stat-${stat}`).value = total;
        });
        
        rollBtn.textContent = 'Stats Rolled!';
        setTimeout(() => {
            rollBtn.textContent = 'Roll Stats (4d6 drop lowest)';
        }, 2000);
    });
}

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
            const response = await fetch('/api/character/create', {
                method: 'POST',
                body: formData
            });
            
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

function initUploadHandlers() {
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('sheet-upload');
    const preview = document.getElementById('upload-preview');
    const previewImg = document.getElementById('preview-img');
    
    uploadArea.addEventListener('click', () => fileInput.click());
    
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });
    
    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('dragover');
    });
    
    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length) handleFile(files[0]);
    });
    
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length) handleFile(e.target.files[0]);
    });
    
    function handleFile(file) {
        if (!file.type.startsWith('image/')) {
            alert('Please upload an image file');
            return;
        }
        
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
        if (!playerName) {
            alert('Please enter your name');
            return;
        }
        
        const file = fileInput.files[0];
        if (!file) return;
        
        const formData = new FormData();
        formData.append('file', file);
        formData.append('player_name', playerName);
        
        try {
            const response = await fetch('/api/character/upload', {
                method: 'POST',
                body: formData
            });
            
            const result = await response.json();
            if (result.status === 'success') {
                currentCharacter = result.character;
                localStorage.setItem('currentCharacter', JSON.stringify(currentCharacter));
                window.location.href = '/app';
            } else if (result.status === 'needs_verification') {
                alert('Character sheet parsed. Please verify the details in the form.');
                document.querySelector('[data-tab="create"]').click();
            }
        } catch (error) {
            console.error('Upload failed:', error);
            alert('Failed to upload character sheet');
        }
    });
}

async function loadExistingCharacters() {
    try {
        const response = await fetch('/api/characters');
        const data = await response.json();
        
        const container = document.getElementById('characters-list');
        
        if (data.characters.length === 0) {
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
    } catch (error) {
        console.error('Failed to load characters:', error);
    }
}