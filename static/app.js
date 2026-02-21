let playerId = null;

async function create() {
    const res = await fetch("/create_character", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            name: document.getElementById("name").value,
            char_class: document.getElementById("class").value,
            race: document.getElementById("race").value,
            background: document.getElementById("background").value
        })
    });

    const data = await res.json();
    playerId = data.player_id;
}

async function send() {
    if (!playerId) return;

    const input = document.getElementById("input");
    const chat = document.getElementById("chat");

    const userMsg = document.createElement("div");
    userMsg.className = "message user";
    userMsg.innerText = input.value;
    chat.appendChild(userMsg);

    const res = await fetch("/chat", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            player_id: playerId,
            message: input.value
        })
    });

    const data = await res.json();

    const dmMsg = document.createElement("div");
    dmMsg.className = "message dm";
    dmMsg.innerText = data.response;
    chat.appendChild(dmMsg);

    input.value = "";
    chat.scrollTop = chat.scrollHeight;
}