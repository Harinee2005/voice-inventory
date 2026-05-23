/**
 * ARIA Chat UI — professional SSE streaming client
 *
 * Key patterns applied:
 *  - AbortController with Stop button for user-initiated stream cancellation
 *  - Frame-based SSE parsing (split on '\n\n') — correct per SSE spec
 *  - Named SSE event dispatch (event: start/status/done/error)
 *  - Keep-alive comment lines (': ping') are silently ignored
 *  - Exponential backoff retry on network error (max 2 retries)
 *  - Inline confirm card (Yes/Cancel) for action='confirm' responses
 *  - Inline update card for action='update' + inventory_updated=true
 *  - Activity log tracking in right panel
 *  - Inventory snapshot auto-refresh after each update
 */

'use strict';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const messagesEl   = document.getElementById('messages');
const textInput    = document.getElementById('text-input');
const sendBtn      = document.getElementById('send-btn');
const stopBtn      = document.getElementById('stop-btn');
const statusPill   = document.getElementById('status-pill');
const streamToggle = document.getElementById('stream-toggle');

const cfgWorker   = document.getElementById('cfg-worker');
const cfgSession  = document.getElementById('cfg-session');
const cfgStorage  = document.getElementById('cfg-storage');
const cfgLocation = document.getElementById('cfg-location');

const settingsBtn  = document.getElementById('settings-btn');
const settingsBar  = document.getElementById('settings-bar');

const invList       = document.getElementById('inv-list');
const invRefreshBtn = document.getElementById('inv-refresh-btn');

const actList  = document.getElementById('act-list');

const memToggleBtn = document.getElementById('mem-toggle-btn');
const memBody      = document.getElementById('mem-body');
const memChevron   = document.getElementById('mem-chevron');
const memList      = document.getElementById('mem-list');
const memRefreshBtn = document.getElementById('mem-refresh-btn');
const memClearBtn   = document.getElementById('mem-clear-btn');

// ── state ─────────────────────────────────────────────────────────────────────
let currentAbortCtrl = null;   // AbortController for the active stream fetch
let isStreaming = false;

// ── settings ─────────────────────────────────────────────────────────────────
settingsBtn.addEventListener('click', () => settingsBar.classList.toggle('open'));

// ── example prompts ──────────────────────────────────────────────────────────
document.querySelectorAll('.ex').forEach(btn => {
  btn.addEventListener('click', () => {
    textInput.value = btn.dataset.msg;
    autoGrow();
    textInput.focus();
  });
});

// ── auto-grow textarea ────────────────────────────────────────────────────────
function autoGrow() {
  textInput.style.height = 'auto';
  textInput.style.height = Math.min(textInput.scrollHeight, 120) + 'px';
}
textInput.addEventListener('input', autoGrow);
textInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!sendBtn.disabled && !isStreaming) sendMessage();
  }
});

// ── input enable/disable ──────────────────────────────────────────────────────
function setStreaming(active) {
  isStreaming = active;
  textInput.disabled = active;
  sendBtn.disabled   = active;
  sendBtn.style.display = active ? 'none'  : 'block';
  stopBtn.style.display = active ? 'block' : 'none';
}

sendBtn.addEventListener('click', sendMessage);
stopBtn.addEventListener('click', () => {
  if (currentAbortCtrl) currentAbortCtrl.abort();
});

// ── utilities ─────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
function setStatus(text, active = false) {
  statusPill.textContent = text;
  statusPill.className   = active ? 'active' : '';
}
function now() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── DOM builders ──────────────────────────────────────────────────────────────
function addUserBubble(text) {
  const el = document.createElement('div');
  el.className = 'msg user';
  el.innerHTML = `<div class="msg-bubble">${esc(text)}</div>`;
  messagesEl.appendChild(el);
  scrollBottom();
}

function showTyping() {
  removeTyping();
  const el = document.createElement('div');
  el.id = 'typing'; el.className = 'typing';
  el.innerHTML = '<span></span><span></span><span></span>';
  messagesEl.appendChild(el);
  scrollBottom();
}
function removeTyping() { document.getElementById('typing')?.remove(); }

function showStatusStep(msg) {
  removeStatusStep();
  const el = document.createElement('div');
  el.id = 'status-step'; el.className = 'status-step';
  el.textContent = msg;
  messagesEl.appendChild(el);
  scrollBottom();
}
function removeStatusStep() { document.getElementById('status-step')?.remove(); }

// ── badge helper ──────────────────────────────────────────────────────────────
function actionBadge(action) {
  const map = { confirm:'b-confirm', update:'b-update', clarify:'b-clarify', flag:'b-flag' };
  const cls = map[action] || 'b-none';
  return `<span class="badge ${cls}">${esc(action)}</span>`;
}

// ── confirm card ──────────────────────────────────────────────────────────────
function buildConfirmCard(items) {
  if (!items || !items.length) return '';
  const rows = items.map(i => `
    <tr>
      <td>${esc(i.item_name || '—')}</td>
      <td>${esc(String(i.quantity ?? ''))} ${esc(i.unit || '')}</td>
      <td>${esc(i.storage_area || '—')}</td>
    </tr>`).join('');
  return `
    <div class="confirm-card">
      <div class="confirm-card-title">⚠ Confirm inventory update</div>
      <table class="card-table">
        <thead><tr><th>Item</th><th>Qty</th><th>Storage</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="card-actions">
        <button class="btn-confirm">Yes, save it</button>
        <button class="btn-cancel">Cancel</button>
      </div>
    </div>`;
}

// ── update card ───────────────────────────────────────────────────────────────
function buildUpdateCard(items) {
  if (!items || !items.length) return '';
  const rows = items.map(i => `
    <tr>
      <td>${esc(i.item_name || '—')}</td>
      <td>${esc(String(i.quantity ?? ''))} ${esc(i.unit || '')}</td>
      <td>${esc(i.storage_area || '—')}</td>
    </tr>`).join('');
  return `
    <div class="update-card">
      <div class="update-card-title">✓ Saved to inventory</div>
      <table class="card-table">
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

// ── ARIA response bubble ──────────────────────────────────────────────────────
function addAriaBubble(payload) {
  const { message, action, data, inventory_updated } = payload;
  const items = data?.items || (data?.item_name ? [data] : []);

  let cardHTML = '';
  if (action === 'confirm' && items.length > 0) {
    cardHTML = buildConfirmCard(items);
  } else if (action === 'update' && inventory_updated && items.length > 0) {
    cardHTML = buildUpdateCard(items);
  }

  const el = document.createElement('div');
  el.className = 'msg aria';
  el.innerHTML = `
    <div class="msg-bubble">${esc(message)}</div>
    ${cardHTML}
    <div class="msg-meta">
      ${actionBadge(action)}
      ${inventory_updated ? '<span class="badge b-saved">✓ saved</span>' : ''}
      <span style="color:var(--text-faint)">${now()}</span>
    </div>`;

  // Wire confirm card buttons
  if (action === 'confirm') {
    el.querySelector('.btn-confirm')?.addEventListener('click', async () => {
      disableCardButtons(el);
      setCardState(el, 'Confirming…');
      await sendDirect('yes');
    });
    el.querySelector('.btn-cancel')?.addEventListener('click', async () => {
      disableCardButtons(el);
      setCardState(el, 'Cancelled.');
      await sendDirect('no');
    });
  }

  messagesEl.appendChild(el);
  scrollBottom();

  if (inventory_updated) {
    recordActivity(items);
    refreshInventory();
  }
}

function disableCardButtons(el) {
  el.querySelectorAll('.btn-confirm, .btn-cancel').forEach(b => b.disabled = true);
}
function setCardState(el, text) {
  const actions = el.querySelector('.card-actions');
  if (actions) actions.innerHTML = `<span style="color:var(--text-dim);font-size:12px">${esc(text)}</span>`;
}

function addErrorBubble(text) {
  const el = document.createElement('div');
  el.className = 'msg aria';
  el.innerHTML = `<div class="msg-bubble" style="color:var(--red)">⚠ ${esc(text)}</div>`;
  messagesEl.appendChild(el);
  scrollBottom();
}

// ── send flow ─────────────────────────────────────────────────────────────────
function buildRequest(text) {
  return {
    text,
    session_id:   cfgSession.value.trim()   || 'session1',
    worker_id:    cfgWorker.value.trim()    || 'worker1',
    storage_area: cfgStorage.value          || '',
    location_name: cfgLocation.value.trim() || '',
  };
}

function sendMessage() {
  const text = textInput.value.trim();
  if (!text) return;
  addUserBubble(text);
  textInput.value = ''; autoGrow();

  if (streamToggle.checked) {
    sendStream(text);
  } else {
    sendSync(text);
  }
}

// Direct send for confirm/cancel responses (no visual duplicate bubble)
async function sendDirect(text) {
  if (streamToggle.checked) {
    await sendStream(text);
  } else {
    await sendSync(text);
  }
}

// ── SSE stream ────────────────────────────────────────────────────────────────
async function sendStream(text, attempt = 0) {
  setStreaming(true);
  setStatus('Connecting…', true);
  showTyping();

  currentAbortCtrl = new AbortController();
  const signal = currentAbortCtrl.signal;

  try {
    const res = await fetch('/api/chat/stream', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(buildRequest(text)),
      signal,
    });

    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let gotDone = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Split on double newline — correct SSE frame boundary per spec
      const frames = buffer.split('\n\n');
      buffer = frames.pop(); // partial last frame stays in buffer

      for (const frame of frames) {
        if (!frame.trim()) continue;

        // Parse the frame line by line
        let dataLine = '';
        let eventName = 'message'; // default SSE event type
        for (const line of frame.split('\n')) {
          if (line.startsWith(':')) continue;           // comment / ping — skip
          if (line.startsWith('event: ')) eventName = line.slice(7).trim();
          if (line.startsWith('data: '))  dataLine  = line.slice(6);
        }

        if (!dataLine) continue;
        let evt;
        try { evt = JSON.parse(dataLine); } catch { continue; }

        // Dispatch by named event type (fall back to JSON 'type' field)
        const etype = eventName !== 'message' ? eventName : (evt.type || 'message');

        if (etype === 'start') {
          // stream opened — nothing to show
        } else if (etype === 'status') {
          removeTyping();
          showStatusStep(evt.message || '');
          setStatus(evt.message || 'Processing…', true);
        } else if (etype === 'done') {
          gotDone = true;
          removeTyping();
          removeStatusStep();
          addAriaBubble(evt.payload || evt);
          setStatus('Ready');
        } else if (etype === 'error') {
          removeTyping();
          removeStatusStep();
          addErrorBubble(evt.message || 'Unknown error');
          setStatus('Error');
        }
      }
    }

    if (!gotDone) {
      removeTyping();
      removeStatusStep();
      setStatus('Ready');
    }

  } catch (e) {
    if (e.name === 'AbortError') {
      // User clicked Stop — clean exit, no error bubble
      removeTyping();
      removeStatusStep();
      setStatus('Stopped');
      return;
    }

    // Network / server error — retry with exponential backoff
    removeTyping();
    removeStatusStep();
    if (attempt < 2) {
      const delay = 1000 * Math.pow(2, attempt); // 1 s, 2 s
      setStatus(`Reconnecting in ${delay / 1000}s…`, true);
      await new Promise(r => setTimeout(r, delay));
      setStatus('Retrying…', true);
      await sendStream(text, attempt + 1);
      return;
    }
    addErrorBubble(`Connection failed: ${e.message}`);
    setStatus('Error');
  } finally {
    currentAbortCtrl = null;
    setStreaming(false);
    textInput.focus();
  }
}

// ── sync (non-streaming) ──────────────────────────────────────────────────────
async function sendSync(text) {
  setStreaming(true);
  setStatus('Processing…', true);
  showTyping();

  try {
    const res     = await fetch('/api/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(buildRequest(text)),
    });
    const payload = await res.json();
    removeTyping();

    if (!res.ok) {
      addErrorBubble(payload.detail || `HTTP ${res.status}`);
      setStatus('Error');
    } else {
      addAriaBubble(payload);
      setStatus('Ready');
    }
  } catch (e) {
    removeTyping();
    addErrorBubble(String(e));
    setStatus('Error');
  } finally {
    setStreaming(false);
    textInput.focus();
  }
}

// ── activity log ──────────────────────────────────────────────────────────────
function recordActivity(items) {
  const empty = actList.querySelector('.act-empty');
  if (empty) empty.remove();

  for (const item of (items || []).slice(0, 3)) {
    const name = item.item_name || '?';
    const qty  = item.quantity != null ? `${item.quantity} ${item.unit || ''}`.trim() : '?';
    const area = item.storage_area || '';
    const el   = document.createElement('div');
    el.className = 'act-item';
    el.innerHTML = `
      <div class="act-item-name">${esc(name)}</div>
      <div class="act-item-detail">
        <span>${esc(qty)}${area ? ' · ' + esc(area) : ''}</span>
        <span class="act-time">${now()}</span>
      </div>`;
    actList.insertBefore(el, actList.firstChild);
  }
  // Keep log to 30 entries
  while (actList.children.length > 30) actList.lastChild.remove();
}

// ── inventory panel ───────────────────────────────────────────────────────────
async function refreshInventory() {
  try {
    const params = new URLSearchParams();
    if (cfgStorage.value) params.set('storage_area', cfgStorage.value);
    const res  = await fetch(`/api/inventory/?${params}`);
    const items = await res.json();
    renderInventory(items);
  } catch { /* silent fail — panel is optional */ }
}

function renderInventory(items) {
  if (!Array.isArray(items) || !items.length) {
    invList.innerHTML = '<div class="inv-empty">No items today.</div>';
    return;
  }

  // Group by storage_area
  const groups = {};
  for (const item of items) {
    const g = item.storage_area || 'General';
    (groups[g] = groups[g] || []).push(item);
  }

  invList.innerHTML = '';
  for (const [group, groupItems] of Object.entries(groups)) {
    const titleEl = document.createElement('div');
    titleEl.className = 'inv-group-title';
    titleEl.textContent = group;
    invList.appendChild(titleEl);

    for (const item of groupItems.slice(0, 12)) {
      const el = document.createElement('div');
      el.className = 'inv-row';
      el.innerHTML = `
        <span class="inv-name" title="${esc(item.item_name)}">${esc(item.item_name)}</span>
        <span class="inv-qty">${esc(String(item.quantity ?? ''))} ${esc(item.unit || '')}</span>`;
      invList.appendChild(el);
    }
    if (groupItems.length > 12) {
      const more = document.createElement('div');
      more.style.cssText = 'padding:2px 6px;color:var(--text-faint);font-size:10px';
      more.textContent = `+${groupItems.length - 12} more`;
      invList.appendChild(more);
    }
  }
}

invRefreshBtn.addEventListener('click', refreshInventory);

// ── memory panel ──────────────────────────────────────────────────────────────
memToggleBtn.addEventListener('click', () => {
  const open = memBody.classList.toggle('open');
  memChevron.textContent = open ? '▾' : '▸';
  if (open) loadMemories();
});

memRefreshBtn.addEventListener('click', loadMemories);
memClearBtn.addEventListener('click', async () => {
  if (!confirm(`Clear all memories for "${cfgWorker.value}"?`)) return;
  await fetch(`/api/memory/entity?worker_id=${encodeURIComponent(cfgWorker.value)}`, { method: 'DELETE' });
  loadMemories();
});

async function loadMemories() {
  memList.innerHTML = '<div style="padding:6px 8px;color:var(--text-faint);font-size:11px">Loading…</div>';
  try {
    const res  = await fetch(`/api/memory?worker_id=${encodeURIComponent(cfgWorker.value)}`);
    const json = await res.json();
    const items = json.items || [];
    if (!items.length) {
      memList.innerHTML = '<div style="padding:6px 8px;color:var(--text-faint);font-size:11px">No memories yet.</div>';
      return;
    }
    memList.innerHTML = '';
    for (const m of items) {
      const text = m.memory || m.text || JSON.stringify(m);
      const id   = m.id || m.memory_id || '';
      const el   = document.createElement('div');
      el.className = 'mem-item';
      el.innerHTML = `<span>${esc(text)}</span>` +
        (id ? `<button data-id="${esc(id)}" title="Delete">×</button>` : '');
      memList.appendChild(el);
    }
    memList.querySelectorAll('button[data-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        await fetch(`/api/memory/item/${encodeURIComponent(btn.dataset.id)}`, { method: 'DELETE' });
        loadMemories();
      });
    });
  } catch (e) {
    memList.innerHTML = `<div style="padding:6px 8px;color:var(--red);font-size:11px">Error: ${esc(String(e))}</div>`;
  }
}

// ── init ──────────────────────────────────────────────────────────────────────
refreshInventory();
textInput.focus();
