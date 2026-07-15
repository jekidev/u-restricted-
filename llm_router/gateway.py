from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .client import LLMResponse, OpenRouterClient
from .config import RouterConfig
from .errors import AllRoutesFailed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gateway")

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")

SYSTEM_PROMPT = """You are a helpful, secure coding assistant. You answer questions clearly and help with defensive security, vulnerability analysis, secure coding practices, and authorized red/blue team training simulations.

Current date: Wednesday, July 15, 2026.

CRITICAL RULES:
- Refuse to provide instructions for harming real people, systems, or infrastructure.
- Do not generate malware, exploits, reverse shells, C2 infrastructure, or credential harvesting tools for unauthorized use.
- When discussing security testing, focus on defensive countermeasures, detection rules, mitigations, and authorized training scenarios.
- Answer in the language the user speaks to you in.
- Be direct, technical, and thorough.
- If a request could facilitate harm, decline it and offer a defensive alternative."""

router: OpenRouterClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global router
    try:
        router = OpenRouterClient()
    except ValueError as e:
        log.error(f"Config: {e}")
        router = None
    yield
    if router:
        await router.aclose()


app = FastAPI(title="OpenRouter Chat", version="2.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatPayload(BaseModel):
    messages: list[dict[str, Any]]
    model: str | list[str] | None = Field(default="auto")
    max_tokens: int | None = None
    temperature: float | None = None


class ConfigPayload(BaseModel):
    api_keys: list[str] | None = None
    base_url: str | None = None
    timeout_seconds: int | None = None
    max_retries_per_route: int | None = None
    cooldown_seconds: int | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    free_only: bool | None = None
    auto_discover: bool | None = None
    model_limit: int | None = None
    models: list[str] | None = None
    app_name: str | None = None
    site_url: str | None = None
    failure_threshold: int | None = None


def validate_messages(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise HTTPException(422, "messages must be a non-empty list")
    clean: list[dict[str, str]] = []
    for item in value[-40:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"system", "user", "assistant"} and isinstance(content, str) and content.strip():
            clean.append({"role": role, "content": content[:30000]})
    if not clean:
        raise HTTPException(422, "No valid messages supplied")
    return clean


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(HTML_UI)


@app.get("/health")
async def health():
    return {"ok": True, "configured": router is not None}


@app.get("/api/models")
@app.get("/v1/models")
async def get_models():
    if router is None:
        raise HTTPException(status_code=503, detail="OpenRouter not configured")
    return {"object": "list", "data": await router.models()}


@app.get("/api/config")
async def get_config():
    if router is None:
        raise HTTPException(status_code=503, detail="OpenRouter not configured")
    return router.config.to_dict()


@app.post("/api/config")
async def post_config(request: Request, payload: ConfigPayload):
    global router
    if router is None:
        raise HTTPException(status_code=503, detail="OpenRouter not configured")
    if ADMIN_TOKEN:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != ADMIN_TOKEN:
            raise HTTPException(status_code=401, detail="Unauthorized")
    current = router.config
    data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    new_config = RouterConfig(
        api_keys=data.get("api_keys", current.api_keys),
        base_url=data.get("base_url", current.base_url),
        timeout_seconds=data.get("timeout_seconds", current.timeout_seconds),
        max_retries_per_route=data.get("max_retries_per_route", current.max_retries_per_route),
        cooldown_seconds=data.get("cooldown_seconds", current.cooldown_seconds),
        max_tokens=data.get("max_tokens", current.max_tokens),
        temperature=data.get("temperature", current.temperature),
        free_only=data.get("free_only", current.free_only),
        auto_discover=data.get("auto_discover", current.auto_discover),
        model_limit=data.get("model_limit", current.model_limit),
        models=data.get("models", current.models),
        app_name=data.get("app_name", current.app_name),
        site_url=data.get("site_url", current.site_url),
        failure_threshold=data.get("failure_threshold", current.failure_threshold),
    )
    router.reconfigure(new_config)
    return router.config.to_dict()


@app.post("/api/chat")
async def chat(payload: ChatPayload):
    if router is None:
        raise HTTPException(status_code=503, detail="OpenRouter not configured")
    model_override: list[str] | None = None
    if payload.model and payload.model != "auto":
        if isinstance(payload.model, str):
            model_override = [m.strip() for m in payload.model.split(",") if m.strip()]
        elif isinstance(payload.model, list):
            model_override = payload.model
    chat_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + validate_messages(payload.messages)
    try:
        result: LLMResponse = await router.chat(
            chat_messages,
            models=model_override,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
        )
        return {"content": result.content, "model": result.model, "usage": result.usage}
    except AllRoutesFailed as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "All routes failed", "attempts": exc.attempts[-8:]},
        )


@app.post("/v1/chat/completions")
async def completions(request: Request):
    if router is None:
        raise HTTPException(status_code=503, detail="OpenRouter not configured")
    body = await request.json()
    requested = body.pop("model", None)
    override = None if not requested or requested in {"auto", "openrouter/free"} else [str(requested)]
    result: LLMResponse = await router.chat(
        validate_messages(body.pop("messages", None)),
        models=override,
        max_tokens=body.pop("max_tokens", body.pop("max_completion_tokens", None)),
        temperature=body.pop("temperature", None),
        extra_body=body,
    )
    return result.raw


HTML_UI = """<!DOCTYPE html>
<html lang="da">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenRouter Chat</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0a0f;color:#e0e0e0;height:100vh;display:flex;flex-direction:column}
nav{background:#0f0f18;border-bottom:1px solid #2a2a3a;display:flex;padding:0 24px}
nav button{background:transparent;border:none;color:#888;padding:14px 20px;cursor:pointer;font-size:14px;border-bottom:2px solid transparent;transition:.2s}
nav button.active{color:#ff4444;border-bottom:2px solid #ff4444}
nav button:hover{color:#e0e0e0}
.pane{display:none;flex-direction:column;flex:1;overflow:hidden}
.pane.active{display:flex}
#chat-pane #chat{flex:1;overflow-y:auto;padding:20px 24px;display:flex;flex-direction:column;gap:12px}
.msg{max-width:82%;padding:10px 16px;border-radius:12px;line-height:1.6;white-space:pre-wrap;word-break:break-word;font-size:14px}
.msg.user{background:#162b22;align-self:flex-end;border-bottom-right-radius:4px;color:#b8e6d0}
.msg.assistant{background:#1a1a2e;align-self:flex-start;border-bottom-left-radius:4px;color:#d0d0e6}
.msg.system{align-self:center;font-style:italic;opacity:.6;font-size:13px;text-align:center;max-width:100%}
#input-area{border-top:1px solid #2a2a3a;padding:16px 24px;display:flex;gap:12px;background:#0f0f18;flex-shrink:0}
#input{flex:1;background:#1a1a2e;border:1px solid #2a2a3a;border-radius:8px;padding:12px 16px;color:#e0e0e0;font-size:14px;outline:none;resize:none;font-family:inherit}
#input:focus{border-color:#ff4444}
#send{background:#ff4444;color:#000;border:none;border-radius:8px;padding:12px 28px;font-weight:700;font-size:14px;cursor:pointer}
#send:disabled{background:#4a2a2a;color:#666;cursor:not-allowed}
#status-bar{display:flex;justify-content:space-between;padding:6px 24px;background:#0a0a0f;border-top:1px solid #1a1a2a;font-size:11px;color:#555;flex-shrink:0}
#settings-pane{padding:40px 24px;max-width:600px;margin:0 auto;width:100%}
#settings-pane h2{color:#ff4444;margin-bottom:24px;font-size:20px}
#settings-pane label{display:block;color:#aaa;margin-bottom:6px;font-size:13px;margin-top:20px}
#settings-pane input,#settings-pane select{width:100%;background:#1a1a2e;border:1px solid #2a2a3a;border-radius:8px;padding:12px 16px;color:#e0e0e0;font-size:14px;outline:none}
#settings-pane input:focus,#settings-pane select:focus{border-color:#ff4444}
#settings-pane .hint{color:#666;font-size:12px;margin-top:4px}
#settings-pane button.primary{background:#ff4444;color:#000;border:none;border-radius:8px;padding:14px 28px;font-weight:700;font-size:14px;cursor:pointer;margin-top:28px;width:100%}
#settings-pane button.primary:hover{background:#ff6666}
#settings-pane .success{background:#162b22;color:#4caf50;padding:10px 16px;border-radius:8px;margin-top:12px;font-size:13px;display:none}
#settings-pane .error-msg{background:#2a1a1a;color:#ff4444;padding:10px 16px;border-radius:8px;margin-top:12px;font-size:13px;display:none}
</style>
</head>
<body>
<nav>
<button id="tab-chat" class="active" onclick="switchTab('chat')">Chat</button>
<button id="tab-settings" onclick="switchTab('settings')">Settings</button>
</nav>

<div id="chat-pane" class="pane active">
<div id="chat"></div>
<div id="input-area">
<textarea id="input" rows="2" placeholder="Skriv din besked..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMsg()}"></textarea>
<button id="send" onclick="sendMsg()">SEND</button>
</div>
<div id="status-bar"><span id="model-indicator">Roterer gennem OpenRouter-modeller</span><span id="error-indicator" style="color:#ff4444;display:none"></span></div>
</div>

<div id="settings-pane" class="pane">
<h2>Indstillinger</h2>
<label>OpenRouter API-nøgler</label>
<input id="api-keys" type="password" placeholder="your-api-key (comma separated for multiple keys)">
<div class="hint">En eller flere API-nøgler adskilt af komma</div>

<label>Admin token</label>
<input id="admin-token" type="password" placeholder="kun påkrævet hvis ADMIN_TOKEN er sat på serveren">
<div class="hint">Beskytter indstillinger mod uautoriserede ændringer på offentlige deploys</div>

<label>Modeltilstand</label>
<select id="model-mode">
<option value="auto">Find gratis modeller automatisk</option>
<option value="fixed">Brug specifikke modeller</option>
</select>
<div id="model-list-wrapper" style="display:none">
<label>Modeller (kommasepareret)</label>
<input id="model-list" type="text" placeholder="google/gemma-3-27b-it:free, meta-llama/llama-3.3-70b-instruct:free">
</div>

<label>Temperatur</label>
<input id="temperature" type="number" step="0.1" min="0" max="2" value="0.9">

<label>Max tokens</label>
<input id="max-tokens" type="number" min="100" max="16000" value="4000">

<button class="primary" onclick="saveSettings()">Gem og genforbind</button>
<div id="settings-success" class="success"></div>
<div id="settings-error" class="error-msg"></div>
</div>

<script>
const messages = [];
let chatEl = document.getElementById('chat');
let inputEl = document.getElementById('input');
let sendBtn = document.getElementById('send');
let modelInd = document.getElementById('model-indicator');
let errInd = document.getElementById('error-indicator');

function switchTab(name) {
    document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    document.getElementById(name + '-pane').classList.add('active');
    document.getElementById('tab-' + name).classList.add('active');
    if (name === 'chat') inputEl.focus();
}

document.getElementById('model-mode').addEventListener('change', function() {
    document.getElementById('model-list-wrapper').style.display = this.value === 'fixed' ? 'block' : 'none';
});

function addMsg(role, text) {
    let d = document.createElement('div');
    d.className = 'msg ' + role;
    d.textContent = text;
    chatEl.appendChild(d);
    d.scrollIntoView({behavior:'smooth'});
    return d;
}

function setError(msg) {
    errInd.textContent = msg;
    errInd.style.display = msg ? 'inline' : 'none';
}

async function sendMsg() {
    let text = inputEl.value.trim();
    if (!text || sendBtn.disabled) return;
    inputEl.value = '';
    sendBtn.disabled = true;
    setError('');
    messages.push({role:'user', content:text});
    addMsg('user', text);
    let thinking = addMsg('system', 'Tænker...');
    try {
        let r = await fetch('/api/chat', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({messages: messages.slice(0, -1), model:'auto', max_tokens:4000, temperature:0.9})
        });
        if (!r.ok) {
            let body = await r.json().catch(() => ({}));
            throw new Error(body.detail?.error || 'HTTP ' + r.status);
        }
        let data = await r.json();
        thinking.remove();
        messages.push({role:'assistant', content:data.content});
        addMsg('assistant', data.content);
        modelInd.textContent = 'Model: ' + (data.model || 'auto');
    } catch (e) {
        thinking.textContent = 'Fejl: ' + e.message;
        setError(e.message);
    } finally {
        sendBtn.disabled = false;
        inputEl.focus();
    }
}

async function loadSettings() {
    try {
        let r = await fetch('/api/config');
        if (!r.ok) return;
        let cfg = await r.json();
        document.getElementById('api-keys').value = '';
        if (cfg.app_name) modelInd.textContent = cfg.app_name;
        document.getElementById('temperature').value = cfg.temperature;
        document.getElementById('max-tokens').value = cfg.max_tokens;
        if (cfg.models_configured > 0) {
            document.getElementById('model-mode').value = 'fixed';
            document.getElementById('model-list-wrapper').style.display = 'block';
            document.getElementById('model-list').value = (cfg.models || []).join(', ');
        }
    } catch (e) { console.warn('Kunne ikke hente indstillinger', e); }
}

async function saveSettings() {
    let successEl = document.getElementById('settings-success');
    let errorEl = document.getElementById('settings-error');
    successEl.style.display = 'none';
    errorEl.style.display = 'none';
    let keys = document.getElementById('api-keys').value.split(',').map(s => s.trim()).filter(Boolean);
    let mode = document.getElementById('model-mode').value;
    let models = mode === 'fixed'
        ? document.getElementById('model-list').value.split(',').map(s => s.trim()).filter(Boolean)
        : [];
    let adminToken = document.getElementById('admin-token').value.trim();
    let payload = {};
    if (keys.length) payload.api_keys = keys;
    if (models.length) payload.models = models;
    payload.temperature = parseFloat(document.getElementById('temperature').value);
    payload.max_tokens = parseInt(document.getElementById('max-tokens').value, 10);
    payload.auto_discover = mode === 'auto';
    let headers = {'Content-Type':'application/json'};
    if (adminToken) headers['Authorization'] = 'Bearer ' + adminToken;
    try {
        let r = await fetch('/api/config', {
            method:'POST',
            headers: headers,
            body: JSON.stringify(payload)
        });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        successEl.textContent = 'Indstillinger gemt og genforbundet.';
        successEl.style.display = 'block';
        await loadSettings();
    } catch (e) {
        errorEl.textContent = 'Fejl: ' + e.message;
        errorEl.style.display = 'block';
    }
}

window.addEventListener('load', loadSettings);
inputEl.focus();
</script>
</body>
</html>"""
