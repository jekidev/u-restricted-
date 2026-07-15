from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RouterConfig:
    api_keys: list[str] = field(default_factory=list)
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: int = 60
    max_retries_per_route: int = 2
    cooldown_seconds: int = 30
    max_tokens: int = 4000
    temperature: float = 0.9
    free_only: bool = True
    auto_discover: bool = True
    model_limit: int = 30
    models: list[str] = field(default_factory=list)
    app_name: str = "OpenRouter Chat"
    site_url: str = ""
    failure_threshold: int = 3

    @classmethod
    def from_env(cls) -> RouterConfig:
        raw = os.environ.get("OPENROUTER_API_KEYS") or os.environ.get("OPENROUTER_API_KEY", "")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            raise ValueError("No API key configured")

        raw_models = os.environ.get("OPENROUTER_MODELS", "")
        model_list = [m.strip() for m in raw_models.split(",") if m.strip()]

        return cls(
            api_keys=keys,
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            timeout_seconds=int(os.environ.get("OPENROUTER_TIMEOUT", "60")),
            max_retries_per_route=int(os.environ.get("OPENROUTER_RETRIES", "2")),
            cooldown_seconds=int(os.environ.get("OPENROUTER_COOLDOWN", "30")),
            max_tokens=int(os.environ.get("OPENROUTER_MAX_TOKENS", "4000")),
            temperature=float(os.environ.get("OPENROUTER_TEMPERATURE", "0.9")),
            free_only=os.environ.get("OPENROUTER_FREE_ONLY", "true").lower() == "true",
            auto_discover=os.environ.get("OPENROUTER_AUTO_DISCOVER", "true").lower() == "true",
            model_limit=int(os.environ.get("OPENROUTER_MODEL_LIMIT", "30")),
            models=model_list,
            app_name=os.environ.get("OPENROUTER_APP_NAME", "OpenRouter Chat"),
            site_url=os.environ.get("OPENROUTER_SITE_URL", ""),
            failure_threshold=int(os.environ.get("OPENROUTER_FAILURE_THRESHOLD", "3")),
        )

    def update_api_keys(self, keys: list[str]) -> None:
        self.api_keys = keys

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_key_count": len(self.api_keys),
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "max_retries_per_route": self.max_retries_per_route,
            "cooldown_seconds": self.cooldown_seconds,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "free_only": self.free_only,
            "auto_discover": self.auto_discover,
            "model_limit": self.model_limit,
            "models_configured": len(self.models),
            "app_name": self.app_name,
            "failure_threshold": self.failure_threshold,
        }
