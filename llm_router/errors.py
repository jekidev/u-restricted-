from __future__ import annotations


class LLMRouterError(RuntimeError):
    """Base exception for the modular LLM router."""


class AllRoutesFailed(LLMRouterError):
    """Raised when all configured keys/routes fail."""

    def __init__(self, attempts: list[dict]) -> None:
        self.attempts = attempts
        super().__init__(f"All {len(attempts)} routes failed after retries")
