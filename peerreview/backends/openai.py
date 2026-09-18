"""OpenAI-compatible Chat Completions backend (also serves local gateways)."""
from __future__ import annotations

import os
from typing import Any, Sequence

from .base import AgentBackend, BackendError, BackendResponse, ChatMessage
from .http import post_json


class OpenAIBackend(AgentBackend):
    provider = "openai"

    def __init__(self, model: str = "gpt-4o", *, api_key_env: str = "OPENAI_API_KEY",
                 base_url: str | None = None, **kwargs: Any):
        super().__init__(model, **kwargs)
        self.api_key_env = api_key_env
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")

    def _send(self, messages: Sequence[ChatMessage], system_prompt: str,
              tools: Sequence[dict[str, Any]] | None) -> BackendResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise BackendError(
                f"{self.api_key_env} is not set; export it or choose another provider")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}]
                        + [m.to_dict() for m in messages],
        }
        # Some newer models reject the legacy field names; send both shapes safely.
        payload["max_completion_tokens" if self.extra.get("use_max_completion_tokens")
                else "max_tokens"] = self.max_tokens
        if self.temperature is not None and not self.extra.get("omit_temperature"):
            payload["temperature"] = self.temperature
        if tools:
            payload["tools"] = list(tools)
        data = post_json(f"{self.base_url}/chat/completions", payload,
                         {"Authorization": f"Bearer {api_key}"}, timeout=self.timeout)
        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        return BackendResponse(text=message.get("content") or "", provider=self.provider,
                               model=self.model, usage=data.get("usage", {}) or {},
                               tool_calls=message.get("tool_calls") or [], raw=data)

    def to_config(self) -> dict[str, Any]:
        config = super().to_config()
        config["params"].update({"api_key_env": self.api_key_env, "base_url": self.base_url})
        return config
