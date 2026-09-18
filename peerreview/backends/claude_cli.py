"""Backend that shells out to the local `claude` CLI.

Useful when the machine has an authenticated Claude Code install but no API key
in the environment. Each call is a fresh one-shot, non-interactive invocation,
so agent contexts stay isolated exactly as with the HTTP backends.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Sequence

from .base import AgentBackend, BackendError, BackendResponse, ChatMessage


class ClaudeCLIBackend(AgentBackend):
    provider = "claude_cli"

    def __init__(self, model: str = "sonnet", *, executable: str = "claude", **kwargs: Any):
        super().__init__(model, **kwargs)
        self.executable = executable

    def _render(self, messages: Sequence[ChatMessage]) -> str:
        if len(messages) == 1:
            return messages[0].content
        lines = []
        for message in messages:
            label = "YOUR PREVIOUS MESSAGE" if message.role == "assistant" else "INPUT"
            lines.append(f"<<<{label}>>>\n{message.content}\n<<<END {label}>>>")
        return "\n\n".join(lines)

    def _send(self, messages: Sequence[ChatMessage], system_prompt: str,
              tools: Sequence[dict[str, Any]] | None) -> BackendResponse:
        if not shutil.which(self.executable):
            raise BackendError(f"`{self.executable}` not found on PATH")
        cmd = [self.executable, "-p", "--output-format", "json", "--max-turns", "1",
               "--restricted", "--system-prompt", system_prompt]
        if self.model:
            cmd += ["--model", self.model]
        try:
            proc = subprocess.run(cmd, input=self._render(messages), capture_output=True,
                                  text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise BackendError(f"claude CLI timed out after {self.timeout}s", retryable=True) from exc
        if proc.returncode != 0:
            raise BackendError(f"claude CLI exited {proc.returncode}: {proc.stderr[:1000]}",
                               retryable=proc.returncode in {1, 143})
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return BackendResponse(text=proc.stdout.strip(), provider=self.provider, model=self.model)
        if data.get("is_error"):
            raise BackendError(f"claude CLI error: {str(data.get('result'))[:500]}", retryable=True)
        return BackendResponse(text=data.get("result", "") or "", provider=self.provider,
                               model=data.get("modelUsage") and self.model or self.model,
                               usage=data.get("usage", {}) or {}, raw=data)

    def to_config(self) -> dict[str, Any]:
        config = super().to_config()
        config["params"]["executable"] = self.executable
        return config
