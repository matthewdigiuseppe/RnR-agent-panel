"""Provider-neutral backend interface.

Orchestration never imports a provider SDK; it only talks to `AgentBackend`.
A backend must be reconstructible from `to_config()` so agent turns can run in
separate OS processes.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass
class ChatMessage:
    role: str                      # "user" | "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class BackendResponse:
    text: str
    provider: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    raw: Any = None


class BackendError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class AgentBackend(ABC):
    """One model endpoint, used by exactly one agent identity."""

    provider: str = "abstract"

    def __init__(self, model: str, *, max_tokens: int = 2048, temperature: float = 0.3,
                 max_retries: int = 4, retry_base_delay: float = 2.0,
                 timeout: float = 180.0, **extra: Any):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.timeout = timeout
        self.extra = extra

    # ------------------------------------------------------------------ api
    @abstractmethod
    def _send(self, messages: Sequence[ChatMessage], system_prompt: str,
              tools: Sequence[dict[str, Any]] | None) -> BackendResponse:
        """Provider-specific single call."""

    def send(self, messages: Sequence[ChatMessage], system_prompt: str,
             tools: Sequence[dict[str, Any]] | None = None) -> BackendResponse:
        """Send with bounded exponential backoff on retryable failures."""
        delay = self.retry_base_delay
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            start = time.time()
            try:
                response = self._send(messages, system_prompt, tools)
                response.latency_ms = response.latency_ms or int((time.time() - start) * 1000)
                return response
            except BackendError as exc:
                last = exc
                if not exc.retryable or attempt == self.max_retries:
                    raise
            except Exception as exc:  # network-level failures are retryable
                last = exc
                if attempt == self.max_retries:
                    raise BackendError(f"{self.provider}: {exc}") from exc
            time.sleep(delay)
            delay *= 2
        raise BackendError(f"{self.provider}: exhausted retries ({last})")

    @property
    def params(self) -> dict[str, Any]:
        return {"max_tokens": self.max_tokens, "temperature": self.temperature, **self.extra}

    def to_config(self) -> dict[str, Any]:
        """JSON-serializable description sufficient to rebuild this backend."""
        return {"provider": self.provider, "model": self.model, "params": self.params}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.provider}:{self.model}>"


_REGISTRY: dict[str, Callable[..., AgentBackend]] = {}


def register_backend(provider: str, factory: Callable[..., AgentBackend]) -> None:
    _REGISTRY[provider] = factory


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def build_backend(provider: str, model: str, params: dict[str, Any] | None = None) -> AgentBackend:
    key = (provider or "").strip().lower()
    if key not in _REGISTRY:
        raise KeyError(f"unknown provider '{provider}'. Available: {', '.join(available_providers())}")
    return _REGISTRY[key](model=model, **(params or {}))
