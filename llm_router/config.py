from __future__ import annotations

from dataclasses import dataclass, field
import os


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class RouterConfig:
    api_keys: list[str]
    models: list[str] = field(default_factory=list)
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 45.0
    max_retries_per_route: int = 1
    cooldown_seconds: float = 60.0
    max_tokens: int = 1400
    temperature: float = 0.7
    free_only: bool = True
    model_limit: int = 30
    app_name: str = "Rotation Chat"
    site_url: str | None = None

    @classmethod
    def from_env(cls) -> "RouterConfig":
        keys = _csv("OPENROUTER_API_KEYS")
        if not keys and os.getenv("OPENROUTER_API_KEY", "").strip():
            keys = [os.environ["OPENROUTER_API_KEY"].strip()]
        if not keys:
            raise ValueError("Set OPENROUTER_API_KEY or OPENROUTER_API_KEYS")
        return cls(
            api_keys=keys,
            models=_csv("OPENROUTER_MODELS"),
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            timeout_seconds=float(os.getenv("OPENROUTER_TIMEOUT", "45")),
            max_retries_per_route=int(os.getenv("OPENROUTER_RETRIES", "1")),
            cooldown_seconds=float(os.getenv("OPENROUTER_COOLDOWN", "60")),
            max_tokens=int(os.getenv("OPENROUTER_MAX_TOKENS", "1400")),
            temperature=float(os.getenv("OPENROUTER_TEMPERATURE", "0.7")),
            free_only=_bool("OPENROUTER_FREE_ONLY", True),
            model_limit=int(os.getenv("OPENROUTER_MODEL_LIMIT", "30")),
            app_name=os.getenv("OPENROUTER_APP_NAME", "Rotation Chat"),
            site_url=os.getenv("OPENROUTER_SITE_URL") or None,
        )
