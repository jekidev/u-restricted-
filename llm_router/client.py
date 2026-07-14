from __future__ import annotations

from dataclasses import dataclass
import asyncio
import time
from typing import Any

import httpx

from .config import RouterConfig
from .errors import AllRoutesFailed


@dataclass(slots=True)
class LLMResponse:
    content: str
    model: str
    usage: dict[str, Any]
    raw: dict[str, Any]


class OpenRouterClient:
    def __init__(self, config: RouterConfig | None = None) -> None:
        self.config = config or RouterConfig.from_env()
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
        )
        self._model_index = 0
        self._key_index = 0
        self._cooldowns: dict[str, float] = {}

    async def __aenter__(self) -> "OpenRouterClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def models(self, refresh: bool = False) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {self.config.api_keys[0]}"}
        response = await self._client.get("/models", headers=headers)
        response.raise_for_status()
        rows = response.json().get("data") or []
        if self.config.free_only:
            rows = [row for row in rows if all(float((row.get("pricing") or {}).get(k) or 0) == 0 for k in ("prompt", "completion", "request"))]
        rows.sort(key=lambda row: int(row.get("context_length") or 0), reverse=True)
        return rows[: self.config.model_limit]

    async def _model_ids(self, override: list[str] | None = None) -> list[str]:
        if override:
            return override
        if self.config.models:
            return self.config.models
        return [row["id"] for row in await self.models() if row.get("id")]

    def _ordered(self, values: list[str], start: int) -> list[str]:
        if not values:
            return []
        start %= len(values)
        return values[start:] + values[:start]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        models: list[str] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        session_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMResponse:
        model_ids = await self._model_ids(models)
        if not model_ids:
            raise AllRoutesFailed([{"error": "No models available"}])

        attempts: list[dict[str, Any]] = []
        ordered_models = self._ordered(model_ids, self._model_index)
        ordered_keys = self._ordered(self.config.api_keys, self._key_index)

        for model in ordered_models:
            for key in ordered_keys:
                route = f"{model}:{key[-8:]}"
                if self._cooldowns.get(route, 0) > time.monotonic():
                    continue
                for retry in range(self.config.max_retries_per_route + 1):
                    headers = {
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "X-Title": self.config.app_name,
                    }
                    if self.config.site_url:
                        headers["HTTP-Referer"] = self.config.site_url
                    body: dict[str, Any] = {
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens or self.config.max_tokens,
                        "temperature": self.config.temperature if temperature is None else temperature,
                    }
                    if extra_body:
                        body.update(extra_body)
                    try:
                        response = await self._client.post("/chat/completions", headers=headers, json=body)
                        if response.status_code in {408, 409, 429} or response.status_code >= 500:
                            raise httpx.HTTPStatusError("retryable", request=response.request, response=response)
                        response.raise_for_status()
                        raw = response.json()
                        content = raw["choices"][0]["message"].get("content") or ""
                        self._model_index = (model_ids.index(model) + 1) % len(model_ids)
                        self._key_index = (self.config.api_keys.index(key) + 1) % len(self.config.api_keys)
                        return LLMResponse(content=content, model=raw.get("model") or model, usage=raw.get("usage") or {}, raw=raw)
                    except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as exc:
                        status = getattr(getattr(exc, "response", None), "status_code", None)
                        attempts.append({"model": model, "key_index": self.config.api_keys.index(key), "status": status, "error": str(exc)})
                        if retry < self.config.max_retries_per_route:
                            await asyncio.sleep(0.75 * (2**retry))
                self._cooldowns[route] = time.monotonic() + self.config.cooldown_seconds

        raise AllRoutesFailed(attempts)

    async def ask(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return await self.chat([{"role": "user", "content": prompt}], **kwargs)
