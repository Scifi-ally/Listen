const $ = (selector) => document.querySelector(selector);

const desktopBridge = window.listenDesktop || null;
document.querySelectorAll('[data-window]').forEach((button) => {
  button.addEventListener('click', () => {
    const action = button.dataset.window;
    if (desktopBridge && typeof desktopBridge[action] === 'function') desktopBridge[action]();
  });
});
if (desktopBridge?.onBackendError) desktopBridge.onBackendError((message) => setStatus(message, 'error'));

const startButton = $('#start-btn');
const stopButton = $('#stop-btn');
const titleInput = $('#session-title');
const statusText = $('#status-text');
const statusDot = $('#status-dot');
const timerLabel = $('#timer');
const transcriptStream = $('#transcript-stream');
const notesStream = $('#notes-stream');
const transcriptCount = $('#transcript-count');
const notesCount = $('#notes-count');
const notesHint = $('#notes-hint');
const exportLink = $('#export-link');
const sessionFile = $('#session-file');
const manualForm = $('#manual-form');
const manualInput = $('#manual-text');
const modelSelect = $('#model-select');
const deviceSelect = $('#device-select');
const recordAudio = $('#record-audio');
const intervalRange = $('#interval-range');
const intervalOutput = $('#interval-output');
const vadRange = $('#vad-range');
const vadOutput = $('#vad-output');
const readinessStrip = $('#readiness-strip');
const readinessText = $('#readiness-text');
const readinessDetail = $('#readiness-detail');

let sessionId = null;
let socket = null;
let startedAt = null;
let timerHandle = null;
let transcriptTotal = 0;
let notesTotal = 0;
const seenTranscript = new Set();
const seenNotes = new Set();

function setStatus(label, mode = 'idle') {
  statusText.textContent = label;
  statusDot.className = `status-dot ${mode}`;
}

function setReadiness(label, mode, detail = '') {
  readinessStrip.className = `readiness-strip ${mode}`;
  readinessText.textContent = label;
  readinessDetail.textContent = detail;
}

function updateTimer() {
  if (!startedAt) return;
  const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  const minutes = String(Math.floor(elapsed / 60)).padStart(2, '0');
  const seconds = String(elapsed % 60).padStart(2, '0');
  timerLabel.textContent = `${minutes}:${seconds}`;
}

function clearEmpty(container) {
  const empty = container.querySelector('.empty-state');
  if (empty) empty.remove();
}

function formatTime(value) {
  if (!value) return 'now';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'now';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function appendTranscript(item) {
  if (!item || !item.text || (item.id && seenTranscript.has(item.id))) return;
  if (item.id) seenTranscript.add(item.id);
  clearEmpty(transcriptStream);
  const row = document.createElement('article');
  row.className = `transcript-item ${item.source === 'manual' ? 'manual' : ''}`;
  row.dataset.id = item.id || '';
  row.innerHTML = `<time class="transcript-time">${formatTime(item.created_at)}</time><p class="transcript-text"></p>`;
  row.querySelector('.transcript-text').textContent = item.text;
  transcriptStream.appendChild(row);
  transcriptStream.scrollTop = transcriptStream.scrollHeight;
  transcriptTotal += 1;
  transcriptCount.textContent = `${transcriptTotal} ${transcriptTotal === 1 ? 'segment' : 'segments'}`;
}

function appendNote(item) {
  if (!item || !item.text || (item.id && seenNotes.has(item.id))) return;
  if (item.id) seenNotes.add(item.id);
  clearEmpty(notesStream);
  const row = document.createElement('article');
  row.className = 'note-item';
  row.dataset.id = item.id || '';
  const marker = item.category === 'definition' ? '≈' : item.category === 'example' ? '◌' : '✦';
  row.innerHTML = `<span class="note-marker">${marker}</span><div><div class="note-category"></div><p></p></div>`;
  row.querySelector('.note-category').textContent = item.category || 'key point';
  row.querySelector('p').textContent = item.text;
  notesStream.appendChild(row);
  notesStream.scrollTop = notesStream.scrollHeight;
  notesTotal += 1;
  notesCount.textContent = `${notesTotal} ${notesTotal === 1 ? 'point' : 'points'}`;
  notesHint.textContent = 'Capturing only what is new';
}

function loadSnapshot(session) {
  transcriptStream.innerHTML = '';
  notesStream.innerHTML = '';
  seenTranscript.clear();
  seenNotes.clear();
  transcriptTotal = 0;
  notesTotal = 0;
  (session.transcript || []).forEach(appendTranscript);
  (session.notes || []).forEach(appendNote);
  if (!session.transcript?.length) transcriptStream.innerHTML = '<div class="empty-state"><span class="empty-icon">⌁</span><p>Waiting for speech…</p><small>Raw transcript segments will appear here.</small></div>';
  if (!session.notes?.length) notesStream.innerHTML = '<div class="empty-state"><span class="empty-icon note">✦</span><p>Key points will collect here.</p><small>Notes update in small batches to preserve context.</small></div>';
}

function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${protocol}://${location.host}/ws/${sessionId}`);
  socket.onopen = () => setStatus('Listening locally', 'live');
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === 'session_snapshot' || message.type === 'session_started') {
      loadSnapshot(message.session);
      startedAt = new Date(message.session.started_at).getTime();
      sessionFile.textContent = `./sessions/${message.session.id}.md`;
      exportLink.href = `/api/sessions/${message.session.id}/export`;
      exportLink.classList.remove('disabled');
    } else if (message.type === 'transcript') {
      appendTranscript(message.item);
    } else if (message.type === 'note') {
      appendNote(message.item);
    } else if (message.type === 'status') {
      if (message.status === 'manual_input') setStatus('Waiting for local mic dependencies', 'warn');
      else if (message.status === 'audio_warning' || message.status === 'audio_overflow') setStatus(message.detail, 'warn');
      else if (message.status === 'extracting_notes') setStatus('Writing notes locally', 'live');
      else if (message.status === 'listening') {
        const asrReady = message.transcriber === 'ready';
        setStatus(asrReady ? 'Listening locally' : 'Mic active · ASR needs setup', asrReady ? 'live' : 'warn');
      }
    } else if (message.type === 'session_stopped') {
      setStatus('Session saved locally', 'idle');
      startedAt = null;
    } else if (message.type === 'error') {
      setStatus(message.message, 'error');
    }
  };
  socket.onclose = () => {
    if (sessionId && !stopButton.disabled) setStatus('Connection closed — data remains local', 'warn');
  };
}

async function startSession() {
  startButton.disabled = true;
  setStatus('Preparing local pipeline…', 'warn');
  try {
    const selectedDevice = deviceSelect.value === '' ? null : deviceSelect.value;
    const response = await fetch('/api/sessions/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: titleInput.value,
        model: modelSelect.value,
        note_interval_seconds: Number(intervalRange.value),
        vad_threshold: Number(vadRange.value) / 1000,
        audio_device: selectedDevice,
        record_audio: recordAudio.checked,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not start session');
    sessionId = data.session.id;
    stopButton.disabled = false;
    manualInput.disabled = false;
    timerHandle = window.setInterval(updateTimer, 1000);
    connectSocket();
  } catch (error) {
    setStatus(error.message, 'error');
    startButton.disabled = false;
  }
}

async function stopSession() {
  if (!sessionId) return;
  stopButton.disabled = true;
  setStatus('Saving session…', 'warn');
  try {
    const response = await fetch(`/api/sessions/${sessionId}/stop`, { method: 'POST' });
    if (!response.ok) throw new Error('The local session could not be stopped cleanly');
  } catch (error) {
    setStatus(`Save warning: ${error.message}`, 'warn');
  }
  if (socket) socket.close();
  window.clearInterval(timerHandle);
  startButton.disabled = false;
  manualInput.disabled = true;
  sessionId = null;
}

manualForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = manualInput.value.trim();
  if (!text || !sessionId) return;
  manualInput.value = '';
  const response = await fetch(`/api/sessions/${sessionId}/transcript`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  });
  if (!response.ok) setStatus('Could not add transcript correction', 'error');
});

intervalRange.addEventListener('input', () => { intervalOutput.value = intervalRange.value; });
vadRange.addEventListener('input', () => { vadOutput.value = (Number(vadRange.value) / 1000).toFixed(3); });
startButton.addEventListener('click', startSession);
stopButton.addEventListener('click', stopSession);
manualInput.disabled = true;

async function loadReadiness() {
  try {
    const configResponse = await fetch('/api/config');
    const config = await configResponse.json();
    const readiness = config.readiness || {};
    const localModels = readiness.local_models || [];
    if (!readiness.audio_dependency) {
      setReadiness('UI ready · microphone dependency not installed', 'warn', 'pip install -r requirements-audio.txt');
    } else if (!readiness.asr_dependency) {
      setReadiness('Microphone ready · Whisper dependency not installed', 'warn', 'pip install -r requirements-audio.txt');
    } else if (!localModels.length) {
      setReadiness('Microphone ready · local Whisper model not found', 'warn', 'place model under ./models');
    } else {
      setReadiness('Local microphone and Whisper model detected', 'ready', localModels.join(', '));
    }

    if (Array.isArray(config.local_models)) {
      config.local_models.forEach((model) => {
        if (!Array.from(modelSelect.options).some((option) => option.value === model)) {
          const option = document.createElement('option');
          option.value = model;
          option.textContent = `${model} · local`;
          modelSelect.appendChild(option);
        }
      });
    }

    const devicesResponse = await fetch('/api/audio/devices');
    const devices = await devicesResponse.json();
    (devices.devices || []).forEach((device) => {
      const option = document.createElement('option');
      option.value = String(device.index);
      option.textContent = `${device.name} · ${device.channels} ch`;
      deviceSelect.appendChild(option);
    });
    if (config.default_llm_model) notesHint.textContent = `Local extractor: ${config.default_llm_model}`;
  } catch (error) {
    setReadiness('Could not read local readiness', 'error', 'Check that the server is running');
  }
}

loadReadiness();
