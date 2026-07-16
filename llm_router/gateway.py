from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .client import LLMResponse, OpenRouterClient
from .config import RouterConfig
from .errors import AllRoutesFailed
from .store import ConversationStore, Message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gateway")

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")

STATIC_DIR = Path(__file__).resolve().parent / "static"

router: OpenRouterClient | None = None
conversation_store: ConversationStore | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global router, conversation_store
    try:
        router = OpenRouterClient()
    except ValueError as e:
        log.error(f"Config: {e}")
        router = None
    conversation_store = ConversationStore()
    try:
        yield
    finally:
        if router:
            await router.aclose()
        if conversation_store:
            await conversation_store.close()


app = FastAPI(
    title="OpenRouter Chat",
    version="2.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatPayload(BaseModel):
    conversation_id: str | None = None
    content: str | None = None
    messages: list[dict[str, Any]] | None = None
    system_prompt: str | None = None
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


class ConversationCreate(BaseModel):
    title: str | None = "Untitled"
    system_prompt: str | None = None


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


def _model_override(payload: ChatPayload) -> list[str] | None:
    if not payload.model or payload.model == "auto":
        return None
    if isinstance(payload.model, str):
        return [m.strip() for m in payload.model.split(",") if m.strip()]
    if isinstance(payload.model, list):
        return payload.model
    return None


def _message_dicts(messages: list[Message]) -> list[dict[str, Any]]:
    return [m.to_dict() for m in messages]


def _json_sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend not installed")
    return FileResponse(index_file)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "configured": router is not None,
        "conversations": conversation_store is not None,
    }


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
    await router.reconfigure(new_config)
    return router.config.to_dict()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def _conversation_summary(c: Conversation) -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "message_count": len(c.messages),
    }


@app.get("/api/conversations")
async def list_conversations(q: str | None = None, limit: int = 100, offset: int = 0):
    if conversation_store is None:
        raise HTTPException(status_code=503, detail="Conversation store not ready")
    if q:
        convs = await conversation_store.search(q, limit=limit)
    else:
        convs = await conversation_store.list(limit=limit, offset=offset)
    return {"object": "list", "data": [_conversation_summary(c) for c in convs]}


@app.post("/api/conversations")
async def create_conversation(payload: ConversationCreate):
    if conversation_store is None:
        raise HTTPException(status_code=503, detail="Conversation store not ready")
    conv = await conversation_store.create(
        title=payload.title or "Untitled",
        system_prompt=payload.system_prompt,
    )
    return conv.to_dict()


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    if conversation_store is None:
        raise HTTPException(status_code=503, detail="Conversation store not ready")
    conv = await conversation_store.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv.to_dict()


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    if conversation_store is None:
        raise HTTPException(status_code=503, detail="Conversation store not ready")
    ok = await conversation_store.delete(conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


async def _load_or_create_conversation(payload: ChatPayload):
    """Resolve a conversation and append a user message if `content` is supplied."""
    if conversation_store is None:
        raise HTTPException(status_code=503, detail="Conversation store not ready")

    if payload.messages:
        # External API style: no persistence implied unless conversation_id is given.
        if payload.conversation_id:
            conv = await conversation_store.get(payload.conversation_id)
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return conv, validate_messages(payload.messages)
        return None, validate_messages(payload.messages)

    if not payload.content or not payload.content.strip():
        raise HTTPException(422, "content is required when messages are not supplied")

    if payload.conversation_id:
        conv = await conversation_store.get(payload.conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conv = await conversation_store.create(system_prompt=payload.system_prompt)

    if payload.system_prompt is not None:
        await conversation_store.update_system_prompt(conv.id, payload.system_prompt)
        conv = await conversation_store.get(conv.id)

    user_msg = Message(
        id=str(uuid.uuid4()),
        role="user",
        content=payload.content.strip(),
    )
    await conversation_store.append_message(conv.id, user_msg, update_title=True)
    conv = await conversation_store.get(conv.id)
    messages = validate_messages(_message_dicts(conv.messages[-40:]))
    return conv, messages


@app.post("/api/chat")
async def chat(payload: ChatPayload):
    if router is None:
        raise HTTPException(status_code=503, detail="OpenRouter not configured")

    conv, messages = await _load_or_create_conversation(payload)
    model_override = _model_override(payload)

    try:
        result: LLMResponse = await router.chat(
            messages,
            models=model_override,
            max_tokens=payload.max_tokens,
            temperature=payload.temperature,
        )
    except AllRoutesFailed as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "All routes failed", "attempts": exc.attempts[-8:]},
        )

    if conv:
        assistant_msg = Message(
            id=str(uuid.uuid4()),
            role="assistant",
            content=result.content,
            model=result.model,
        )
        await conversation_store.append_message(conv.id, assistant_msg)

    response: dict[str, Any] = {
        "content": result.content,
        "model": result.model,
        "usage": result.usage,
    }
    if conv:
        response["conversation_id"] = conv.id
    return response


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatPayload):
    if router is None:
        raise HTTPException(status_code=503, detail="OpenRouter not configured")

    conv, messages = await _load_or_create_conversation(payload)
    model_override = _model_override(payload)

    async def event_generator():
        if conv:
            yield _json_sse("conversation", {"id": conv.id, "title": conv.title})

        full_content = ""
        saved = False
        model_used = payload.model or "auto"
        try:
            async for token in router.stream(
                messages,
                models=model_override,
                max_tokens=payload.max_tokens,
                temperature=payload.temperature,
            ):
                full_content += token
                yield _json_sse("token", {"token": token})
            yield _json_sse(
                "done",
                {"content": full_content, "model": model_used, "usage": {}},
            )
        except AllRoutesFailed as exc:
            yield _json_sse(
                "error",
                {"error": "All routes failed", "attempts": exc.attempts[-8:]},
            )
            return
        finally:
            if conv and conversation_store and full_content and not saved:
                assistant_msg = Message(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    content=full_content,
                    model=model_used,
                )
                await conversation_store.append_message(conv.id, assistant_msg)
                saved = True

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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
