"""Provider-neutral model call contracts shared by AI capabilities."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelCallMetrics:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None


__all__ = ["ModelCallMetrics"]
