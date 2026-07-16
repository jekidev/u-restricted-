from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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

STATIC_DIR = Path(__file__).resolve().parent / "static"

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
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend not installed")
    return FileResponse(index_file)


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
    chat_messages = validate_messages(payload.messages)
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

