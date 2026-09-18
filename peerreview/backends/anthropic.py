"""Anthropic Messages API backend (no SDK dependency)."""
from __future__ import annotations

import os
from typing import Any, Sequence

from .base import AgentBackend, BackendError, BackendResponse, ChatMessage
from .http import post_json

DEFAULT_VERSION = "2023-06-01"


class AnthropicBackend(AgentBackend):
    provider = "anthropic"

    def __init__(self, model: str = "claude-sonnet-5", *, api_key_env: str = "ANTHROPIC_API_KEY",
                 base_url: str | None = None, api_version: str = DEFAULT_VERSION, **kwargs: Any):
        super().__init__(model, **kwargs)
        self.api_key_env = api_key_env
        self.base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL")
                         or "https://api.anthropic.com").rstrip("/")
        self.api_version = api_version

    def _send(self, messages: Sequence[ChatMessage], system_prompt: str,
              tools: Sequence[dict[str, Any]] | None) -> BackendResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise BackendError(
                f"{self.api_key_env} is not set; export it or choose another provider")
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system_prompt,
            "messages": [m.to_dict() for m in messages],
        }
        if tools:
            payload["tools"] = list(tools)
        for key in ("top_p", "top_k", "stop_sequences"):
            if key in self.extra:
                payload[key] = self.extra[key]
        data = post_json(f"{self.base_url}/v1/messages", payload,
                         {"x-api-key": api_key, "anthropic-version": self.api_version},
                         timeout=self.timeout)
        text_parts, tool_calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(block)
        return BackendResponse(text="".join(text_parts), provider=self.provider, model=self.model,
                               usage=data.get("usage", {}) or {}, tool_calls=tool_calls, raw=data)

    def to_config(self) -> dict[str, Any]:
        config = super().to_config()
        config["params"].update({"api_key_env": self.api_key_env, "base_url": self.base_url})
        return config
