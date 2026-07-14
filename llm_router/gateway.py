from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .client import OpenRouterClient
from .errors import AllRoutesFailed

router: OpenRouterClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global router
    router = OpenRouterClient()
    yield
    await router.aclose()


app = FastAPI(title="OpenRouter Rotation Model", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


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


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/models")
@app.get("/v1/models")
async def models():
    assert router is not None
    rows = await router.models()
    return {"object": "list", "data": rows}


@app.post("/api/chat")
async def chat(request: Request):
    assert router is not None
    body = await request.json()
    requested = body.get("model")
    override = None if not requested or requested in {"auto", "openrouter/free"} else [str(requested)]
    try:
        result = await router.chat(
            validate_messages(body.get("messages")),
            models=override,
            max_tokens=min(int(body.get("max_tokens", 1200)), 4000),
            temperature=float(body.get("temperature", 0.7)),
            session_id=str(body.get("session_id", "web-chat"))[:100],
        )
        return {"content": result.content, "model": result.model, "usage": result.usage}
    except AllRoutesFailed as exc:
        raise HTTPException(503, {"message": "All configured routes failed", "attempts": exc.attempts[-8:]}) from exc


@app.post("/v1/chat/completions")
async def completions(request: Request):
    assert router is not None
    body = await request.json()
    requested = body.pop("model", None)
    override = None if not requested or requested in {"auto", "openrouter/free"} else [str(requested)]
    result = await router.chat(
        validate_messages(body.pop("messages", None)),
        models=override,
        max_tokens=body.pop("max_tokens", body.pop("max_completion_tokens", None)),
        temperature=body.pop("temperature", None),
        extra_body=body,
    )
    return result.raw
