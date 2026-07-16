(() => {
  'use strict';

  const LS_CONVERSATIONS = 'nexus_conversations';
  const LS_ACCESS_CODE = 'nexus_access_code';

  const state = {
    conversations: [],
    currentId: null,
    messages: [],
    settings: null,
    models: [],
    authenticated: false,
    accessCode: localStorage.getItem(LS_ACCESS_CODE) || '',
    isSending: false,
    isRevealing: false,
    controller: null,
    searchQuery: '',
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function uuid() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  }

  function timeLabel(value) {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function saveConversations() {
    localStorage.setItem(LS_CONVERSATIONS, JSON.stringify(state.conversations));
  }

  function loadLocal() {
    try {
      state.conversations = JSON.parse(localStorage.getItem(LS_CONVERSATIONS) || '[]');
    } catch {
      state.conversations = [];
    }
    state.authenticated = !!state.accessCode;
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
    if (name === 'settings') {
      $('#api-key').focus();
    }
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
      localStorage.removeItem(LS_ACCESS_CODE);
      updateAuthUI();
      showToast('Logged out', 'info');
    } else {
      $('#login-modal').classList.add('open');
    }
  }

  async function fetchJson(path, opts = {}) {
    const res = await fetch(path, opts);
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
      // Server may be unconfigured; leave defaults.
      console.warn('config', e);
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
      select.value = current;
    } catch (e) {
      console.warn('models', e);
    }
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
      const res = await fetch('/api/config', {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
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

  function createConversation() {
    if (!requireAuth()) return;
    state.currentId = null;
    state.messages = [];
    renderMessages();
    setView('chat');
    $('#chat-input').focus();
  }

  function updateCurrentConversation() {
    if (!state.currentId) {
      if (state.messages.length === 0) return;
      const firstUser = state.messages.find(m => m.role === 'user');
      const title = firstUser ? firstUser.content.slice(0, 40) : 'Untitled';
      const conv = { id: uuid(), title, updatedAt: Date.now(), messages: [] };
      state.conversations.unshift(conv);
      state.currentId = conv.id;
    }
    const conv = state.conversations.find(c => c.id === state.currentId);
    if (!conv) return;
    const firstUser = state.messages.find(m => m.role === 'user');
    conv.title = firstUser ? firstUser.content.slice(0, 40) : conv.title;
    conv.updatedAt = Date.now();
    conv.messages = state.messages.slice(-100);
    saveConversations();
    renderConversations();
  }

  function openConversation(id) {
    const conv = state.conversations.find(c => c.id === id);
    if (!conv) return;
    state.currentId = conv.id;
    state.messages = conv.messages.slice();
    renderMessages();
    setView('chat');
    $('#chat-input').focus();
  }

  function deleteConversation(id, event) {
    event.stopPropagation();
    state.conversations = state.conversations.filter(c => c.id !== id);
    if (state.currentId === id) {
      state.currentId = null;
      state.messages = [];
      renderMessages();
    }
    saveConversations();
    renderConversations();
    showToast('Transmission deleted', 'info');
  }

  function filteredConversations() {
    const q = state.searchQuery.toLowerCase().trim();
    if (!q) return state.conversations;
    return state.conversations.filter(c =>
      c.title.toLowerCase().includes(q) ||
      c.messages.some(m => m.content.toLowerCase().includes(q))
    );
  }

  function renderConversations() {
    const list = $('#conversation-list');
    list.innerHTML = '';
    const items = filteredConversations();
    $('#conv-count').textContent = items.length;

    if (items.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'text-xs text-white-28 p-4';
      empty.textContent = state.searchQuery ? 'No results found.' : 'No saved transmissions.';
      list.appendChild(empty);
      return;
    }

    for (const conv of items) {
      const active = conv.id === state.currentId;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `conversation-link ${active ? 'conversation-link-active' : ''}`;
      btn.innerHTML = `
        <svg class="icon icon-4 mt-0.5 shrink-0"><use href="#icon-message"/></svg>
        <span class="min-w-0 flex-1">
          <span class="block truncate text-xs text-white-65">${escapeHtml(conv.title)}</span>
          <span class="mt-1 block text-[9px] text-white-24">${timeLabel(conv.updatedAt)}</span>
        </span>
        <svg class="icon icon-4 shrink-0 opacity-25"><use href="#icon-chevron-right"/></svg>
      `;
      btn.addEventListener('click', () => openConversation(conv.id));
      list.appendChild(btn);
    }
  }

  function renderFiles() {
    const grid = $('#files-conversations');
    const count = $('#saved-count');
    grid.innerHTML = '';
    count.textContent = state.conversations.length;

    if (state.conversations.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'glass-block text-xs text-white-28';
      empty.textContent = 'No saved conversations.';
      grid.appendChild(empty);
    } else {
      for (const conv of state.conversations) {
        const card = document.createElement('button');
        card.type = 'button';
        card.className = 'file-card';
        card.innerHTML = `
          <svg class="icon icon-4 text-white-45"><use href="#icon-message"/></svg>
          <span class="mt-4 block truncate text-xs text-white-68">${escapeHtml(conv.title)}</span>
          <span class="mt-1 block text-[9px] text-white-25">${timeLabel(conv.updatedAt)}</span>
        `;
        card.addEventListener('click', () => openConversation(conv.id));
        grid.appendChild(card);
      }
    }

    const exportsEl = $('#files-exports');
    const exportCount = $('#export-count');
    exportsEl.innerHTML = '';
    exportCount.textContent = '0';
    const emptyExport = document.createElement('div');
    emptyExport.className = 'glass-block text-xs text-white-28';
    emptyExport.textContent = 'Exports appear here after generation.';
    exportsEl.appendChild(emptyExport);
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function createMessageElement(message) {
    const row = document.createElement('article');
    row.className = `message-row ${message.role === 'user' ? 'message-row-user' : ''}`;
    row.dataset.id = message.id;
    const isUser = message.role === 'user';
    const label = isUser ? 'Operator' : 'System response';
    const modelTag = message.model ? `// ${message.model}` : '';
    row.innerHTML = `
      <div class="message-avatar ${isUser ? 'message-avatar-user' : ''}">
        <svg class="icon icon-4"><use href="#icon-${isUser ? 'user' : 'bot'}"/></svg>
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
      inner.appendChild($('#empty-state'));
      $('#empty-state').style.display = 'flex';
      updateActiveModel('Awaiting model route');
      return;
    }
    $('#empty-state').style.display = 'none';
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
    const sending = state.isSending || state.isRevealing;
    $('#send-btn').classList.toggle('hidden', sending);
    $('#stop-btn').classList.toggle('hidden', !sending);
    $('#loading-icon').classList.toggle('hidden', !sending);
    $('#chat-input').disabled = state.isSending;
  }

  function revealText(el, text, onDone) {
    const tokens = text.match(/\S+|\s+/g) || [];
    el.textContent = '';
    state.isRevealing = true;
    updateSendButton();
    let i = 0;
    const chunkSize = 2;
    function step() {
      if (!state.isRevealing || i >= tokens.length) {
        el.textContent = text;
        state.isRevealing = false;
        updateSendButton();
        if (onDone) onDone();
        return;
      }
      const end = Math.min(i + chunkSize, tokens.length);
      el.textContent += tokens.slice(i, end).join('');
      i = end;
      scrollToBottom();
      setTimeout(step, 10);
    }
    step();
  }

  function stopGeneration() {
    if (state.isRevealing) {
      state.isRevealing = false;
    }
    if (state.controller) {
      state.controller.abort();
      state.controller = null;
    }
    state.isSending = false;
    updateSendButton();
  }

  async function sendMessage(content) {
    if (!requireAuth()) return;
    if (state.isSending || state.isRevealing) return;

    if (!state.settings || state.settings.api_key_count === 0) {
      showToast('Add an OpenRouter API key in Settings first', 'error');
      setView('settings');
      return;
    }

    const userMsg = { id: uuid(), role: 'user', content };
    state.messages.push(userMsg);
    updateCurrentConversation();
    renderMessages();

    const inner = $('#chat-messages-inner');
    $('#empty-state').style.display = 'none';

    const assistantId = uuid();
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
    state.controller = new AbortController();

    const selectedModel = $('#model-select').value;
    const payload = {
      messages: state.messages.slice(-40),
      model: selectedModel === 'auto' ? 'auto' : selectedModel,
      max_tokens: parseInt($('#max-tokens').value, 10),
      temperature: parseFloat($('#temperature').value),
    };

    try {
      const data = await fetchJson('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: state.controller.signal,
      });

      thinkingRow.remove();
      const assistantMsg = { id: assistantId, role: 'assistant', content: '', model: data.model };
      state.messages.push(assistantMsg);
      const row = createMessageElement(assistantMsg);
      inner.appendChild(row);
      const contentEl = row.querySelector('.message-content');

      updateActiveModel(data.model);

      revealText(contentEl, data.content || '', () => {
        assistantMsg.content = contentEl.textContent;
        updateCurrentConversation();
      });
    } catch (e) {
      if (e.name === 'AbortError') {
        thinkingRow.remove();
      } else {
        thinkingRow.querySelector('#thinking').textContent = `Error: ${e.message}`;
        showToast(`Chat failed: ${e.message}`, 'error');
      }
      state.messages = state.messages.filter(m => m.id !== assistantId);
      updateCurrentConversation();
    } finally {
      state.isSending = false;
      state.controller = null;
      updateSendButton();
      $('#chat-input').focus();
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
        const role = m.role === 'user' ? 'Operator' : 'Assistant';
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
    showToast(`${filename} generated`, 'success');
  }

  function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
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
      localStorage.setItem(LS_ACCESS_CODE, code);
      $('#access-code').value = '';
      updateAuthUI();
      showToast('Access granted', 'success');
    });

    $('#new-convo').addEventListener('click', createConversation);

    $('#search-input').addEventListener('input', (e) => {
      state.searchQuery = e.target.value;
      renderConversations();
    });
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

    $(document.body).addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeSidebar();
        $('#login-modal').classList.remove('open');
      }
    });
  }

  function init() {
    loadLocal();
    updateAuthUI();
    renderConversations();
    renderMessages();
    bindEvents();
    loadConfig();
    if (!state.authenticated) $('#login-modal').classList.add('open');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
