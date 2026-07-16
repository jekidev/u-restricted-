(() => {
  'use strict';

  const LS_ACCESS_CODE = 'nexus_access_code';
  const LS_SYSTEM_PROMPT = 'nexus_system_prompt';

  const state = {
    conversations: [],
    currentId: null,
    messages: [],
    settings: null,
    models: [],
    authenticated: false,
    accessCode: '',
    systemPrompt: '',
    isSending: false,
    controller: null,
    searchQuery: '',
    exports: [],
  };

  let emptyStateTemplate = null;

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function loadLocal() {
    try {
      state.accessCode = localStorage.getItem(LS_ACCESS_CODE) || '';
    } catch {
      state.accessCode = '';
    }
    try {
      state.systemPrompt = localStorage.getItem(LS_SYSTEM_PROMPT) || '';
    } catch {
      state.systemPrompt = '';
    }
    state.authenticated = !!state.accessCode;
  }

  function uuid() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  }

  function timeLabel(value) {
    let ts = Number(value);
    if (!Number.isFinite(ts)) return '—';
    if (ts < 1e10) ts *= 1000; // server returns Unix seconds
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function showToast(message, type = 'info') {
    const toast = $('#toast');
    toast.textContent = message;
    toast.className = `toast ${type}`;
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => toast.classList.remove('show'), 3500);
  }

  function setView(name) {
    $$('.view').forEach(el => el.classList.remove('active'));
    $(`#${name}-view`).classList.add('active');
    $$('[data-view]').forEach(btn => {
      const active = btn.dataset.view === name;
      btn.classList.toggle('hud-tab-active', active);
      btn.setAttribute('aria-pressed', String(active));
    });
    closeSidebar();
    if (name === 'files') renderFiles();
    if (name === 'settings') $('#api-key').focus();
  }

  function openSidebar() {
    $('#sidebar').classList.add('open');
    $('#sidebar-backdrop').classList.add('open');
  }

  function closeSidebar() {
    $('#sidebar').classList.remove('open');
    $('#sidebar-backdrop').classList.remove('open');
  }

  function updateAuthUI() {
    const nameEl = $('#user-name');
    const stateEl = $('#user-state');
    const iconUse = $('#auth-icon use');
    if (state.authenticated) {
      nameEl.textContent = 'Operator';
      stateEl.textContent = 'SESSION VERIFIED';
      iconUse.setAttribute('href', '#icon-log-out');
      $('#login-modal').classList.remove('open');
    } else {
      nameEl.textContent = 'Guest operator';
      stateEl.textContent = 'OFFLINE';
      iconUse.setAttribute('href', '#icon-log-in');
    }
  }

  function requireAuth() {
    if (!state.authenticated) {
      $('#login-modal').classList.add('open');
      return false;
    }
    return true;
  }

  function toggleAuth() {
    if (state.authenticated) {
      state.authenticated = false;
      state.accessCode = '';
      try { localStorage.removeItem(LS_ACCESS_CODE); } catch {}
      updateAuthUI();
      showToast('Logged out', 'info');
    } else {
      $('#login-modal').classList.add('open');
    }
  }

  async function fetchJson(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (state.accessCode) headers.Authorization = `Bearer ${state.accessCode}`;
    if (opts.headers) {
      for (const [key, value] of Object.entries(opts.headers)) {
        if (value === undefined || value === null) delete headers[key];
        else headers[key] = value;
      }
    }
    const res = await fetch(path, { ...opts, headers });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const msg = body.detail?.error || body.detail || body.message || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  async function loadConfig() {
    try {
      state.settings = await fetchJson('/api/config');
      const badge = $('#key-badge');
      if (state.settings.api_key_count > 0) {
        badge.textContent = 'SECURED';
        badge.classList.add('secured');
      } else {
        badge.textContent = 'PENDING';
        badge.classList.remove('secured');
      }
      $('#temperature').value = Number(state.settings.temperature ?? 0.9).toFixed(1);
      $('#max-tokens').value = state.settings.max_tokens ?? 4000;
      await loadModels();
    } catch (e) {
      console.warn('config', e);
      state.settings = null;
    }
  }

  async function loadModels() {
    try {
      const data = await fetchJson('/api/models');
      state.models = Array.isArray(data.data) ? data.data : [];
      const select = $('#model-select');
      const current = select.value;
      select.innerHTML = '<option value="auto">AUTO — ROUND ROBIN + FAILOVER</option>';
      for (const model of state.models) {
        const name = model.name || model.id;
        const ctx = model.context_length ? ` · ${(model.context_length / 1000).toFixed(0)}K` : '';
        const opt = document.createElement('option');
        opt.value = model.id;
        opt.textContent = `${name}${ctx}`;
        select.appendChild(opt);
      }
      select.value = current || 'auto';
    } catch (e) {
      console.warn('models', e);
    }
  }

  async function loadConversations(q = '') {
    try {
      const url = q ? `/api/conversations?q=${encodeURIComponent(q)}&limit=100` : '/api/conversations?limit=100';
      const data = await fetchJson(url);
      state.conversations = Array.isArray(data.data) ? data.data : [];
      renderConversations();
      $('#saved-count').textContent = state.conversations.length;
    } catch (e) {
      console.warn('conversations', e);
      showToast('Could not load conversations', 'error');
    }
  }

  async function createConversation() {
    if (!requireAuth()) return;
    try {
      const conv = await fetchJson('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: 'Untitled',
          system_prompt: state.systemPrompt.trim() || undefined,
        }),
      });
      state.currentId = conv.id;
      state.messages = conv.messages || [];
      await loadConversations();
      renderMessages();
      setView('chat');
      $('#chat-input').focus();
    } catch (e) {
      showToast(`Failed to create conversation: ${e.message}`, 'error');
    }
  }

  async function openConversation(id) {
    try {
      const conv = await fetchJson(`/api/conversations/${id}`);
      state.currentId = conv.id;
      state.messages = Array.isArray(conv.messages) ? conv.messages : [];
      renderMessages();
      renderConversations();
      closeSidebar();
      $('#chat-input').focus();
    } catch (e) {
      showToast(`Failed to open conversation: ${e.message}`, 'error');
    }
  }

  async function deleteConversation(id) {
    try {
      await fetchJson(`/api/conversations/${id}`, { method: 'DELETE' });
      if (state.currentId === id) {
        state.currentId = null;
        state.messages = [];
        renderMessages();
      }
      await loadConversations(state.searchQuery);
      renderFiles();
      showToast('Transmission deleted', 'info');
    } catch (e) {
      showToast(`Failed to delete: ${e.message}`, 'error');
    }
  }

  async function runHealthCheck() {
    try {
      const data = await fetchJson('/api/health/routes');
      const lines = [
        `Route health: ${data.ok ? 'OK' : 'DEGRADED'}`,
        `Base URL: ${data.base_url}`,
        `Timeout: ${data.timeout_seconds}s`,
        `API keys: ${data.api_key_count}`,
        `Models available: ${data.models_available}`,
        `Routes in cooldown: ${data.routes_in_cooldown}`,
      ];
      if (Array.isArray(data.sample_models) && data.sample_models.length) {
        lines.push(`Sample models: ${data.sample_models.join(', ')}`);
      }
      state.messages.push({ id: uuid(), role: 'system', content: lines.join('\n') });
    } catch (e) {
      state.messages.push({ id: uuid(), role: 'system', content: `Health check failed: ${e.message}` });
    }
    renderMessages();
  }

  function renderConversations() {
    const list = $('#conversation-list');
    list.innerHTML = '';
    $('#conv-count').textContent = state.conversations.length;

    if (state.conversations.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'text-xs text-white-28 p-4';
      empty.textContent = state.searchQuery ? 'No results found.' : 'No saved transmissions.';
      list.appendChild(empty);
      return;
    }

    for (const conv of state.conversations) {
      const active = conv.id === state.currentId;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `conversation-link ${active ? 'conversation-link-active' : ''}`;
      btn.innerHTML = `
        <svg class="icon icon-4 mt-0.5 shrink-0"><use href="#icon-message"/></svg>
        <span class="min-w-0 flex-1">
          <span class="block truncate text-xs text-white-65">${escapeHtml(conv.title || 'Untitled')}</span>
          <span class="mt-1 block text-[9px] text-white-24">${timeLabel(conv.updated_at)} · ${conv.message_count || 0} msgs</span>
        </span>
        <svg class="icon icon-4 shrink-0 opacity-25"><use href="#icon-chevron-right"/></svg>
      `;
      btn.addEventListener('click', () => openConversation(conv.id));
      list.appendChild(btn);
    }
  }

  function renderFiles() {
    const grid = $('#files-conversations');
    grid.innerHTML = '';
    $('#saved-count').textContent = state.conversations.length;

    if (state.conversations.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'glass-block text-xs text-white-28';
      empty.textContent = 'No saved conversations.';
      grid.appendChild(empty);
    } else {
      for (const conv of state.conversations) {
        const card = document.createElement('div');
        card.className = 'file-card';
        card.innerHTML = `
          <svg class="icon icon-4 text-white-45"><use href="#icon-message"/></svg>
          <span class="mt-4 block truncate text-xs text-white-68">${escapeHtml(conv.title || 'Untitled')}</span>
          <span class="mt-1 block text-[9px] text-white-25">${timeLabel(conv.updated_at)} · ${conv.message_count || 0} msgs</span>
          <button type="button" class="delete-convo hud-icon-button" data-id="${conv.id}" title="Delete" aria-label="Delete conversation">
            <svg class="icon icon-4"><use href="#icon-x"/></svg>
          </button>
        `;
        card.querySelector('.delete-convo').addEventListener('click', (e) => {
          e.stopPropagation();
          deleteConversation(conv.id);
        });
        card.addEventListener('click', () => openConversation(conv.id));
        grid.appendChild(card);
      }
    }

    const exportsEl = $('#files-exports');
    exportsEl.innerHTML = '';
    $('#export-count').textContent = state.exports.length;

    if (state.exports.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'glass-block text-xs text-white-28';
      empty.textContent = 'Exports appear here after generation.';
      exportsEl.appendChild(empty);
    } else {
      for (const exp of state.exports) {
        const row = document.createElement('a');
        row.href = exp.url;
        row.download = exp.filename;
        row.className = 'file-card';
        row.innerHTML = `
          <svg class="icon icon-4 text-white-45"><use href="#icon-file-text"/></svg>
          <span class="mt-4 block truncate text-xs text-white-68">${escapeHtml(exp.filename)}</span>
          <span class="mt-1 block text-[9px] text-white-25">${timeLabel(exp.date)}</span>
        `;
        exportsEl.appendChild(row);
      }
    }
  }

  function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function createMessageElement(message) {
    const row = document.createElement('article');
    row.className = `message-row ${message.role === 'user' ? 'message-row-user' : ''}`;
    row.dataset.id = message.id || uuid();
    const isUser = message.role === 'user';
    const label = isUser ? 'Operator' : (message.role === 'system' ? 'System directive' : 'System response');
    const modelTag = message.model ? `// ${message.model}` : '';
    row.innerHTML = `
      <div class="message-avatar ${isUser ? 'message-avatar-user' : ''}">
        <svg class="icon icon-4"><use href="#icon-${isUser ? 'user' : (message.role === 'system' ? 'shield-check' : 'bot')}"/></svg>
      </div>
      <div class="message-shell ${isUser ? 'message-shell-user' : ''}">
        <div class="message-meta">
          <span>${label}</span>
          <span class="truncate text-white-20">${escapeHtml(modelTag)}</span>
        </div>
        <div class="message-content">${escapeHtml(message.content)}</div>
      </div>
    `;
    return row;
  }

  function renderMessages() {
    const inner = $('#chat-messages-inner');
    inner.innerHTML = '';
    if (state.messages.length === 0) {
      if (emptyStateTemplate) {
        const empty = emptyStateTemplate.cloneNode(true);
        empty.style.display = 'flex';
        empty.querySelectorAll('[data-prompt]').forEach((btn) => {
          btn.addEventListener('click', () => sendMessage(btn.dataset.prompt));
        });
        inner.appendChild(empty);
      }
      updateActiveModel('Awaiting model route');
      return;
    }
    for (const msg of state.messages) {
      inner.appendChild(createMessageElement(msg));
    }
    scrollToBottom();
  }

  function scrollToBottom() {
    const container = $('#chat-messages');
    container.scrollTop = container.scrollHeight;
  }

  function updateActiveModel(model) {
    $('#active-model').textContent = model || 'Awaiting model route';
  }

  function updateSendButton() {
    $('#send-btn').classList.toggle('hidden', state.isSending);
    $('#stop-btn').classList.toggle('hidden', !state.isSending);
    $('#loading-icon').classList.toggle('hidden', !state.isSending);
    $('#chat-input').disabled = state.isSending;
  }

  function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }

  function getSystemPromptToSend() {
    const currentSys = state.messages.find(m => m.role === 'system')?.content || '';
    const prompt = state.systemPrompt.trim();
    if (prompt === currentSys) return null;
    return prompt; // may be empty string to clear an existing system prompt
  }

  async function streamChat(payload, handlers) {
    state.controller = new AbortController();
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: state.controller.signal,
    });

    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const text = await res.text();
        const j = JSON.parse(text);
        msg = j.detail?.error || j.detail || j.error || text || msg;
      } catch {}
      throw new Error(msg);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let eventName = '';
    let dataLines = [];

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (let line of lines) {
        line = line.replace(/\r$/, '');
        if (line === '') {
          const data = dataLines.join('\n');
          if (eventName && data) {
            try {
              const parsed = JSON.parse(data);
              if (eventName === 'conversation' && handlers.onConversation) handlers.onConversation(parsed);
              else if (eventName === 'token' && handlers.onToken) handlers.onToken(parsed.token || '');
              else if (eventName === 'done' && handlers.onDone) handlers.onDone(parsed);
              else if (eventName === 'error' && handlers.onError) handlers.onError(parsed);
            } catch (e) {
              console.warn('SSE parse error', e, data);
            }
          }
          eventName = '';
          dataLines = [];
        } else if (line.startsWith('event: ')) {
          eventName = line.slice(7);
        } else if (line.startsWith('data: ')) {
          dataLines.push(line.slice(6));
        }
      }
    }

    // Flush any remaining event if stream ended without a blank line.
    if (eventName && dataLines.length) {
      const data = dataLines.join('\n');
      try {
        const parsed = JSON.parse(data);
        if (eventName === 'done' && handlers.onDone) handlers.onDone(parsed);
      } catch (e) {
        console.warn('SSE trailing parse error', e, data);
      }
    }
  }

  async function sendMessage(content) {
    if (state.isSending) return;
    if (!requireAuth()) return;

    const trimmed = content.trim();
    const lower = trimmed.toLowerCase();
    if (lower === '/health' || lower === '/routes' || lower === 'tjek openrouter route-sundhed') {
      await runHealthCheck();
      return;
    }

    if (!state.settings || state.settings.api_key_count === 0) {
      showToast('Add an OpenRouter API key in Settings first', 'error');
      setView('settings');
      return;
    }

    const selectedModel = $('#model-select').value;
    const payload = {
      content,
      conversation_id: state.currentId || undefined,
      model: selectedModel === 'auto' ? 'auto' : selectedModel,
      max_tokens: parseInt($('#max-tokens').value, 10),
      temperature: parseFloat($('#temperature').value),
    };
    const systemPrompt = getSystemPromptToSend();
    if (systemPrompt !== null) payload.system_prompt = systemPrompt;

    const userMsg = { id: uuid(), role: 'user', content };
    state.messages.push(userMsg);
    renderMessages();

    const inner = $('#chat-messages-inner');
    const thinkingRow = document.createElement('article');
    thinkingRow.className = 'message-row';
    thinkingRow.innerHTML = `
      <div class="message-avatar"><svg class="icon icon-4"><use href="#icon-bot"/></svg></div>
      <div class="message-shell">
        <div class="message-meta"><span>System response</span></div>
        <div class="message-content" id="thinking">▍</div>
      </div>
    `;
    inner.appendChild(thinkingRow);
    scrollToBottom();

    state.isSending = true;
    updateSendButton();

    let assistantRow = null;
    let assistantContentEl = null;

    try {
      await streamChat(payload, {
        onConversation(conv) {
          state.currentId = conv.id;
          const existing = state.conversations.find(c => c.id === conv.id);
          if (existing) {
            existing.title = conv.title || existing.title;
          } else {
            state.conversations.unshift({
              id: conv.id,
              title: conv.title || 'Untitled',
              updated_at: Date.now() / 1000,
              message_count: 1,
            });
          }
          renderConversations();
        },
        onToken(token) {
          if (!assistantRow) {
            thinkingRow.remove();
            assistantRow = document.createElement('article');
            assistantRow.className = 'message-row';
            assistantRow.innerHTML = `
              <div class="message-avatar"><svg class="icon icon-4"><use href="#icon-bot"/></svg></div>
              <div class="message-shell">
                <div class="message-meta"><span>System response</span></div>
                <div class="message-content"></div>
              </div>
            `;
            inner.appendChild(assistantRow);
            assistantContentEl = assistantRow.querySelector('.message-content');
          }
          assistantContentEl.textContent += token;
          scrollToBottom();
        },
        async onDone(data) {
          state.isSending = false;
          updateSendButton();
          updateActiveModel(data.model || selectedModel);
          if (thinkingRow.parentNode) thinkingRow.remove();
          if (state.currentId) {
            await openConversation(state.currentId);
            await loadConversations();
          }
          $('#chat-input').focus();
        },
        onError(err) {
          state.isSending = false;
          updateSendButton();
          if (thinkingRow.parentNode) thinkingRow.remove();
          const msg = err.error || JSON.stringify(err);
          showToast(`Chat failed: ${msg}`, 'error');
          $('#chat-input').focus();
        },
      });
    } catch (e) {
      if (e.name === 'AbortError') {
        if (thinkingRow.parentNode) thinkingRow.remove();
        showToast('Generation stopped', 'info');
        if (state.currentId) {
          await openConversation(state.currentId);
          await loadConversations();
        }
      } else {
        if (thinkingRow.parentNode) thinkingRow.remove();
        showToast(`Chat failed: ${e.message}`, 'error');
      }
    } finally {
      state.isSending = false;
      state.controller = null;
      updateSendButton();
      $('#chat-input').focus();
    }
  }

  function stopGeneration() {
    if (state.controller) {
      state.controller.abort();
      state.controller = null;
    }
  }

  function exportConversation(format) {
    if (state.messages.length === 0) {
      showToast('Start or open a conversation first', 'error');
      return;
    }
    const model = $('#active-model').textContent;
    const date = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
    let blob;
    let filename;
    if (format === 'md') {
      const lines = [`# Transmission — ${date}\n`, `**Model:** ${model}\n`];
      for (const m of state.messages) {
        const role = m.role === 'user' ? 'Operator' : (m.role === 'system' ? 'System' : 'Assistant');
        lines.push(`## ${role}${m.model ? ` (${m.model})` : ''}\n\n${m.content}\n`);
      }
      blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
      filename = `transmission-${date}.md`;
    } else {
      blob = new Blob([JSON.stringify({ model, date, messages: state.messages }, null, 2)], { type: 'application/json' });
      filename = `transmission-${date}.json`;
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    state.exports.unshift({ filename, url, date: Date.now() / 1000 });
    showToast(`${filename} generated`, 'success');
    renderFiles();
  }

  async function saveSettings() {
    const successEl = $('#settings-success');
    const errorEl = $('#settings-error');
    successEl.classList.add('hidden');
    errorEl.classList.add('hidden');

    const key = $('#api-key').value.trim();
    const model = $('#model-select').value;
    const temp = parseFloat($('#temperature').value);
    const maxTokens = parseInt($('#max-tokens').value, 10);
    const adminToken = $('#admin-token').value.trim();
    state.systemPrompt = $('#system-prompt').value;
    try { localStorage.setItem(LS_SYSTEM_PROMPT, state.systemPrompt); } catch {}

    const payload = {
      temperature: temp,
      max_tokens: maxTokens,
      auto_discover: model === 'auto',
    };
    if (key) payload.api_keys = key.split(',').map(k => k.trim()).filter(Boolean);
    if (model !== 'auto') payload.models = [model];

    const headers = { 'Content-Type': 'application/json' };
    if (adminToken) headers.Authorization = `Bearer ${adminToken}`;

    try {
      await fetchJson('/api/config', {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });
      successEl.textContent = 'Route saved and router reconfigured.';
      successEl.classList.remove('hidden');
      $('#api-key').value = '';
      await loadConfig();
      showToast('OpenRouter route secured', 'success');
    } catch (e) {
      errorEl.textContent = `Failed: ${e.message}`;
      errorEl.classList.remove('hidden');
      showToast(`Settings failed: ${e.message}`, 'error');
    }
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function bindEvents() {
    $('#menu-toggle').addEventListener('click', () => {
      if ($('#sidebar').classList.contains('open')) closeSidebar();
      else openSidebar();
    });
    $('#sidebar-backdrop').addEventListener('click', closeSidebar);

    $$('[data-view]').forEach(btn => {
      btn.addEventListener('click', () => setView(btn.dataset.view));
    });

    $('#auth-btn').addEventListener('click', toggleAuth);
    $('#verify-code').addEventListener('click', () => {
      const code = $('#access-code').value.trim();
      if (!code) return;
      state.accessCode = code;
      state.authenticated = true;
      try { localStorage.setItem(LS_ACCESS_CODE, code); } catch {}
      $('#access-code').value = '';
      updateAuthUI();
      showToast('Access granted', 'success');
    });

    $('#new-convo').addEventListener('click', createConversation);

    $('#search-input').addEventListener('input', debounce((e) => {
      state.searchQuery = e.target.value;
      loadConversations(state.searchQuery);
    }, 250));
    $('#search-btn').addEventListener('click', () => {
      $('#search-input').focus();
    });

    $('#chat-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const input = $('#chat-input');
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      input.style.height = 'auto';
      sendMessage(text);
    });

    $('#chat-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        $('#chat-form').requestSubmit();
      }
    });
    $('#chat-input').addEventListener('input', (e) => autoResize(e.target));

    $('#stop-btn').addEventListener('click', stopGeneration);

    $('#save-settings').addEventListener('click', saveSettings);

    $$('[data-prompt]').forEach(btn => {
      btn.addEventListener('click', () => sendMessage(btn.dataset.prompt));
    });

    $$('[data-export]').forEach(btn => {
      btn.addEventListener('click', () => exportConversation(btn.dataset.export));
    });

    document.body.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeSidebar();
        $('#login-modal').classList.remove('open');
      }
    });
  }

  function init() {
    const originalEmpty = $('#empty-state');
    if (originalEmpty) {
      emptyStateTemplate = originalEmpty.cloneNode(true);
      emptyStateTemplate.removeAttribute('id');
    }
    loadLocal();
    $('#system-prompt').value = state.systemPrompt;
    updateAuthUI();
    bindEvents();
    renderMessages();
    loadConfig();
    loadConversations();
    if (!state.authenticated) $('#login-modal').classList.add('open');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
