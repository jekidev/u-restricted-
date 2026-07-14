class LLMRouterError(RuntimeError):
    """Base exception for the modular LLM router."""


class AllRoutesFailed(LLMRouterError):
    """Raised when all configured keys/routes fail."""

    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        summary = "; ".join(
            f"key#{a.get('key_index')} status={a.get('status')} error={a.get('error')}"
            for a in attempts
        )
        super().__init__(f"All OpenRouter routes failed: {summary}")
