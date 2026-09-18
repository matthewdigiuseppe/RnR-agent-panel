"""Backend registry. Adding a provider means implementing AgentBackend and
registering a factory here (or calling `register_backend` from your own code)."""
from __future__ import annotations

from .anthropic import AnthropicBackend
from .base import (AgentBackend, BackendError, BackendResponse, ChatMessage,
                   available_providers, build_backend, register_backend)
from .claude_cli import ClaudeCLIBackend
from .mock import MockBackend
from .openai import OpenAIBackend

register_backend("anthropic", AnthropicBackend)
register_backend("openai", OpenAIBackend)
register_backend("claude_cli", ClaudeCLIBackend)
register_backend("mock", MockBackend)

__all__ = [
    "AgentBackend", "BackendError", "BackendResponse", "ChatMessage", "MockBackend",
    "AnthropicBackend", "OpenAIBackend", "ClaudeCLIBackend",
    "available_providers", "build_backend", "register_backend",
]
