"""Agent runtimes and their execution isolation.

Each agent is a stable identity with its own system prompt, its own backend
instance and its own context state. Turns can be executed serially, in threads,
or in genuinely separate OS processes (`isolation: process`), which is why
backends must be reconstructible from `to_config()`.
"""
from __future__ import annotations

import concurrent.futures as futures
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from .backends import build_backend
from .backends.base import AgentBackend, ChatMessage
from .models import AgentSpec


@dataclass
class TurnRequest:
    agent_id: str
    phase: str
    round: int
    messages: list[ChatMessage]
    focus_issue_ids: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    agent_id: str
    text: str
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
    request: TurnRequest | None = None


def _run_isolated(payload: dict[str, Any]) -> dict[str, Any]:
    """Entry point for process isolation. Must stay module-level and picklable.

    Every failure, including a misconfigured provider, is returned rather than
    raised: one agent must never take the run down with it.
    """
    start = time.time()
    try:
        backend = build_backend(payload["provider"], payload["model"], payload["params"])
        messages = [ChatMessage(**m) for m in payload["messages"]]
        response = backend.send(messages, payload["system_prompt"])
        return {"text": response.text, "usage": response.usage,
                "tool_calls": response.tool_calls,
                "latency_ms": response.latency_ms or int((time.time() - start) * 1000),
                "error": None}
    except Exception as exc:  # returned rather than raised so one agent cannot kill a run
        return {"text": "", "usage": {}, "tool_calls": [],
                "latency_ms": int((time.time() - start) * 1000),
                "error": f"{type(exc).__name__}: {exc}"}


class Agent:
    """One participant: stable identity + isolated context + its own backend."""

    def __init__(self, spec: AgentSpec, backend: AgentBackend):
        self.spec = spec
        self.backend = backend

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def role(self) -> str:
        return self.spec.role

    @property
    def system_prompt(self) -> str:
        return self.spec.system_prompt

    def payload(self, messages: Sequence[ChatMessage]) -> dict[str, Any]:
        config = self.backend.to_config()
        return {"provider": config["provider"], "model": config["model"],
                "params": config["params"], "system_prompt": self.system_prompt,
                "messages": [m.to_dict() for m in messages]}

    def run(self, messages: Sequence[ChatMessage]) -> TurnResult:
        start = time.time()
        try:
            response = self.backend.send(messages, self.system_prompt)
            return TurnResult(agent_id=self.id, text=response.text, usage=response.usage,
                              tool_calls=response.tool_calls,
                              latency_ms=response.latency_ms or int((time.time() - start) * 1000))
        except Exception as exc:
            return TurnResult(agent_id=self.id, text="", error=f"{type(exc).__name__}: {exc}",
                              latency_ms=int((time.time() - start) * 1000))


class AgentPool:
    """Executes agent turns with the configured isolation."""

    def __init__(self, agents: dict[str, Agent], *, isolation: str = "thread",
                 max_workers: int = 5):
        self.agents = agents
        self.isolation = isolation
        self.max_workers = max_workers

    def __getitem__(self, agent_id: str) -> Agent:
        return self.agents[agent_id]

    def get(self, agent_id: str) -> Agent | None:
        return self.agents.get(agent_id)

    @property
    def reviewers(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.role == "reviewer"]

    def run_one(self, request: TurnRequest) -> TurnResult:
        agent = self.agents[request.agent_id]
        if self.isolation == "process":
            return self._run_process([request])[0]
        result = agent.run(request.messages)
        result.request = request
        return result

    def run_many(self, requests: Sequence[TurnRequest]) -> list[TurnResult]:
        """Run independent turns concurrently; contexts never touch each other."""
        if not requests:
            return []
        if len(requests) == 1 or self.isolation == "serial":
            return [self.run_one(request) for request in requests]
        if self.isolation == "process":
            return self._run_process(requests)
        results: list[TurnResult | None] = [None] * len(requests)
        with futures.ThreadPoolExecutor(max_workers=min(self.max_workers, len(requests))) as pool:
            future_map = {pool.submit(self.agents[r.agent_id].run, r.messages): i
                          for i, r in enumerate(requests)}
            for future in futures.as_completed(future_map):
                index = future_map[future]
                result = future.result()
                result.request = requests[index]
                results[index] = result
        return [r for r in results if r is not None]

    def _run_process(self, requests: Sequence[TurnRequest]) -> list[TurnResult]:
        payloads = [self.agents[r.agent_id].payload(r.messages) for r in requests]
        results: list[TurnResult] = []
        with futures.ProcessPoolExecutor(max_workers=min(self.max_workers, len(requests))) as pool:
            for request, raw in zip(requests, pool.map(_run_isolated, payloads)):
                results.append(TurnResult(agent_id=request.agent_id, text=raw["text"],
                                          usage=raw["usage"],
                                          tool_calls=raw.get("tool_calls") or [],
                                          latency_ms=raw["latency_ms"], error=raw["error"],
                                          request=request))
        return results
