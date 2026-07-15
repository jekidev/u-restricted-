from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
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
            timeout=httpx.Timeout(self.config.timeout_seconds, connect=15, read=45),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=30),
        )
        self._model_index = 0
        self._key_index = 0
        self._cooldowns: dict[str, float] = {}
        self._failure_counts: dict[str, int] = {}
        self._model_cache: list[dict[str, Any]] = []
        self._model_cache_time = 0.0

    async def __aenter__(self) -> OpenRouterClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def reconfigure(self, config: RouterConfig) -> None:
        self.config = config
        self._model_cache = []
        self._model_cache_time = 0
        self._cooldowns.clear()
        self._failure_counts.clear()

    async def models(self, refresh: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not refresh and self._model_cache and (now - self._model_cache_time) < 300:
            return self._model_cache
        for key in self.config.api_keys:
            try:
                headers = {"Authorization": f"Bearer {key}"}
                resp = await self._client.get("/models", headers=headers)
                resp.raise_for_status()
                rows = resp.json().get("data") or []
                if self.config.free_only:
                    rows = [
                        r
                        for r in rows
                        if all(
                            float((r.get("pricing") or {}).get(k, 0)) == 0
                            for k in ("prompt", "completion", "request")
                        )
                    ]
                rows.sort(key=lambda r: int(r.get("context_length", 0)), reverse=True)
                self._model_cache = rows[: self.config.model_limit]
                self._model_cache_time = now
                return self._model_cache
            except Exception as e:
                self._log_key("Model fetch failed", key, e)
                continue
        return self._model_cache or []

    def _log_key(self, prefix: str, key: str, exc: Exception) -> None:
        suffix = key[-8:] if len(key) >= 8 else key
        self._log(f"{prefix} with key ...{suffix}: {exc}")

    def _log(self, msg: str) -> None:
        import logging
        logging.getLogger("gateway").warning(msg)

    async def _model_ids(self, override: list[str] | None = None) -> list[str]:
        if override:
            return override
        if self.config.models:
            return self.config.models
        if not self.config.auto_discover:
            return self.config.models or []
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
                if self._failure_counts.get(route, 0) >= self.config.failure_threshold:
                    if route not in self._cooldowns or self._cooldowns[route] < time.monotonic():
                        self._cooldowns[route] = time.monotonic() + self.config.cooldown_seconds * 2
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
                        resp = await self._client.post("/chat/completions", headers=headers, json=body)
                        if resp.status_code in {408, 409, 429} or resp.status_code >= 500:
                            raise httpx.HTTPStatusError(
                                f"retryable status={resp.status_code}",
                                request=resp.request,
                                response=resp,
                            )
                        resp.raise_for_status()
                        raw = resp.json()
                        if "choices" not in raw or not raw["choices"]:
                            raise ValueError("No choices in response")
                        content = raw["choices"][0]["message"].get("content") or ""

                        self._failure_counts[route] = 0
                        self._model_index = (model_ids.index(model) + 1) % len(model_ids)
                        self._key_index = (self.config.api_keys.index(key) + 1) % len(self.config.api_keys)
                        self._log(f"OK {model} ({len(content)} chars)")

                        return LLMResponse(
                            content=content,
                            model=raw.get("model") or model,
                            usage=raw.get("usage") or {},
                            raw=raw,
                        )

                    except httpx.TimeoutException as exc:
                        self._log(f"Timeout {route} ({retry+1})")
                        attempts.append({"model": model, "status": "timeout", "error": str(exc)})
                    except httpx.HTTPStatusError as exc:
                        s = exc.response.status_code
                        self._log(f"HTTP {s} {route} ({retry+1})")
                        attempts.append({"model": model, "status": s, "error": str(exc)})
                    except (httpx.RequestError, ValueError, KeyError) as exc:
                        self._log(f"Error {route} ({retry+1}): {exc}")
                        attempts.append({"model": model, "status": "error", "error": str(exc)})
                    if retry < self.config.max_retries_per_route:
                        await asyncio.sleep(1.0 * (2**retry))

                self._failure_counts[route] = self._failure_counts.get(route, 0) + 1
                self._cooldowns[route] = time.monotonic() + self.config.cooldown_seconds

        raise AllRoutesFailed(attempts)

    async def ask(self, prompt: str, **kwargs: Any) -> LLMResponse:
        return await self.chat([{"role": "user", "content": prompt}], **kwargs)
