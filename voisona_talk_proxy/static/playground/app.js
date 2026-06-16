const elements = {
  username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  rememberCredentials: document.querySelector("#rememberCredentials"),
  voiceSelect: document.querySelector("#voiceSelect"),
  text: document.querySelector("#text"),
  payload: document.querySelector("#payload"),
  loadVoicesButton: document.querySelector("#loadVoicesButton"),
  synthesizeButton: document.querySelector("#synthesizeButton"),
  downloadLink: document.querySelector("#downloadLink"),
  audio: document.querySelector("#audio"),
  status: document.querySelector("#status"),
};

let voices = [];
let audioUrl = null;
let syncing = false;
const credentialsStorageKey = "voisonaTalkProxy.credentials";

function setStatus(message) {
  elements.status.textContent = message;
}

function authHeaders() {
  const username = elements.username.value;
  const password = elements.password.value;
  if (!username && !password) {
    return {};
  }
  const credentials = new TextEncoder().encode(`${username}:${password}`);
  let binary = "";
  credentials.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return {
    Authorization: `Basic ${btoa(binary)}`,
  };
}

function loadStoredCredentials() {
  try {
    const raw = localStorage.getItem(credentialsStorageKey);
    if (!raw) {
      return;
    }

    const credentials = JSON.parse(raw);
    elements.username.value = credentials.username || "";
    elements.password.value = credentials.password || "";
    elements.rememberCredentials.checked = true;
  } catch {
    localStorage.removeItem(credentialsStorageKey);
  }
}

function saveStoredCredentials() {
  try {
    if (!elements.rememberCredentials.checked) {
      localStorage.removeItem(credentialsStorageKey);
      return;
    }

    localStorage.setItem(
      credentialsStorageKey,
      JSON.stringify({
        username: elements.username.value,
        password: elements.password.value,
      }),
    );
  } catch (error) {
    setStatus(`Failed to save credentials.\n${error.message}`);
  }
}

function selectedVoice() {
  const index = Number(elements.voiceSelect.value);
  if (!Number.isInteger(index) || !voices[index]) {
    return {};
  }

  const voice = voices[index];
  return {
    language: voice.languages?.[0] || "ja_JP",
    voice_name: voice.voice_name,
    voice_version: voice.voice_version,
  };
}

function syncPayload() {
  if (syncing) {
    return;
  }
  syncing = true;
  const payload = {
    text: elements.text.value,
    language: "ja_JP",
    ...selectedVoice(),
  };
  elements.payload.value = JSON.stringify(payload, null, 2);
  syncing = false;
}

function findVoiceIndex(payload) {
  if (!payload.voice_name) {
    return "";
  }

  const index = voices.findIndex((voice) => {
    if (voice.voice_name !== payload.voice_name) {
      return false;
    }
    return !payload.voice_version || voice.voice_version === payload.voice_version;
  });
  return index >= 0 ? String(index) : "";
}

function syncFromPayload() {
  if (syncing) {
    return;
  }

  let payload;
  try {
    payload = JSON.parse(elements.payload.value);
  } catch (error) {
    setStatus(`Payload is not valid JSON.\n${error.message}`);
    return;
  }

  syncing = true;
  if (typeof payload.text === "string") {
    elements.text.value = payload.text;
  } else if (typeof payload.analyzed_text === "string") {
    elements.text.value = "";
  }
  elements.voiceSelect.value = findVoiceIndex(payload);
  syncing = false;
  setStatus("Payload applied to form.");
}

async function loadVoices() {
  elements.loadVoicesButton.disabled = true;
  setStatus("Loading voices...");

  try {
    const response = await fetch("/voices", {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${await response.text()}`);
    }

    const body = await response.json();
    voices = body.items || [];
    elements.voiceSelect.replaceChildren(new Option("Default / manually configured", ""));
    voices.forEach((voice, index) => {
      const label = `${voice.voice_name} ${voice.voice_version || ""}`.trim();
      elements.voiceSelect.add(new Option(label, String(index)));
    });
    syncFromPayload();
    setStatus(
      voices.length
        ? `Loaded ${voices.length} voices. Select one from the Voice list.`
        : "No voices returned. You can edit the payload manually.",
    );
  } catch (error) {
    elements.voiceSelect.replaceChildren(new Option("Failed to load voices", ""));
    setStatus(`Failed to load voices.\n${error.message}`);
  } finally {
    elements.loadVoicesButton.disabled = false;
  }
}

async function synthesize() {
  elements.synthesizeButton.disabled = true;
  elements.downloadLink.hidden = true;
  setStatus("Synthesizing...");

  try {
    const payload = JSON.parse(elements.payload.value);
    const response = await fetch("/speech-syntheses", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${await response.text()}`);
    }

    const blob = await response.blob();
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
    }
    audioUrl = URL.createObjectURL(blob);
    elements.audio.src = audioUrl;
    elements.downloadLink.href = audioUrl;
    elements.downloadLink.hidden = false;
    setStatus(`Ready. ${blob.size} bytes.`);
  } catch (error) {
    setStatus(`Failed to synthesize.\n${error.message}`);
  } finally {
    elements.synthesizeButton.disabled = false;
  }
}

elements.loadVoicesButton.addEventListener("click", loadVoices);
elements.synthesizeButton.addEventListener("click", synthesize);
elements.voiceSelect.addEventListener("change", syncPayload);
elements.text.addEventListener("input", syncPayload);
elements.payload.addEventListener("input", syncFromPayload);
elements.username.addEventListener("input", saveStoredCredentials);
elements.password.addEventListener("input", saveStoredCredentials);
elements.rememberCredentials.addEventListener("change", saveStoredCredentials);

loadStoredCredentials();
syncPayload();
loadVoices();
