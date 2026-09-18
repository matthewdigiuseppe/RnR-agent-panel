"""Deterministic mock backend.

Tests and dry runs use this so an end-to-end deliberation costs nothing and is
byte-for-byte reproducible. Rules match against the rendered prompt (which
always carries an `AGENT:` / `PHASE:` / `ROUND:` header), so no provider-specific
hooks leak into the orchestrator.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from .base import AgentBackend, BackendResponse, ChatMessage

DEFAULT_REPLY = "NO FURTHER COMMENT. I have nothing to add beyond what is already in the transcript."


class MockBackend(AgentBackend):
    """Rule-driven canned responses.

    script = {
      "default": "...",
      "rules": [
        {"all": ["AGENT: Reviewer1", "PHASE: independent_review"],
         "any": [...], "regex": "...",
         "respond": "..."},
        {"all": ["AGENT: Editor"], "respond_sequence": ["turn 1", "turn 2"]}
      ]
    }
    """

    provider = "mock"

    def __init__(self, model: str = "mock-1", *, script: dict[str, Any] | None = None,
                 script_path: str | None = None, default: str = DEFAULT_REPLY, **kwargs: Any):
        super().__init__(model, **kwargs)
        self.script_path = script_path
        if script is None and script_path:
            script = json.loads(Path(script_path).read_text(encoding="utf-8"))
        self.script = script or {}
        self.default = self.script.get("default", default)
        self._counters: dict[int, int] = {}
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _matches(rule: dict[str, Any], prompt: str) -> bool:
        for needle in rule.get("all", []):
            if needle not in prompt:
                return False
        any_of = rule.get("any")
        if any_of and not any(needle in prompt for needle in any_of):
            return False
        pattern = rule.get("regex")
        if pattern and not re.search(pattern, prompt, re.S):
            return False
        return True

    def _send(self, messages: Sequence[ChatMessage], system_prompt: str,
              tools: Sequence[dict[str, Any]] | None) -> BackendResponse:
        prompt = "\n\n".join(m.content for m in messages)
        self.calls.append({"system": system_prompt, "prompt": prompt})
        for index, rule in enumerate(self.script.get("rules", [])):
            if not self._matches(rule, prompt):
                continue
            if "respond_sequence" in rule:
                sequence = rule["respond_sequence"]
                position = self._counters.get(index, 0)
                self._counters[index] = position + 1
                text = sequence[min(position, len(sequence) - 1)]
            else:
                text = rule.get("respond", self.default)
            return BackendResponse(text=text, provider=self.provider, model=self.model,
                                   usage={"input_tokens": len(prompt) // 4,
                                          "output_tokens": len(text) // 4})
        return BackendResponse(text=self.default, provider=self.provider, model=self.model,
                               usage={"input_tokens": len(prompt) // 4,
                                      "output_tokens": len(self.default) // 4})

    def to_config(self) -> dict[str, Any]:
        config = super().to_config()
        if self.script_path:
            config["params"]["script_path"] = self.script_path
        else:
            config["params"]["script"] = self.script
        return config
