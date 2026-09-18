"""Requirement 11: provider implementations are swappable."""
from __future__ import annotations

import pytest

from peerreview.backends import (AgentBackend, AnthropicBackend, BackendError, BackendResponse,
                                 ChatMessage, ClaudeCLIBackend, MockBackend, OpenAIBackend,
                                 available_providers, build_backend, register_backend)
from peerreview.db import Store
from peerreview.orchestrator import Orchestrator
from peerreview.reporting import Reporter

from conftest import make_project


class EchoBackend(AgentBackend):
    """A third-party provider written entirely against the public interface."""

    provider = "echo"

    def __init__(self, model: str = "echo-1", *, prefix: str = "ECHO", **kwargs):
        super().__init__(model, **kwargs)
        self.prefix = prefix

    def _send(self, messages, system_prompt, tools):
        return BackendResponse(text=f"{self.prefix}: {messages[-1].content[:40]}",
                               provider=self.provider, model=self.model,
                               usage={"input_tokens": 1, "output_tokens": 1})

    def to_config(self):
        config = super().to_config()
        config["params"]["prefix"] = self.prefix
        return config


def test_registry_builds_every_shipped_provider():
    assert {"anthropic", "openai", "claude_cli", "mock"} <= set(available_providers())
    assert isinstance(build_backend("anthropic", "claude-sonnet-5"), AnthropicBackend)
    assert isinstance(build_backend("openai", "gpt-4o"), OpenAIBackend)
    assert isinstance(build_backend("claude_cli", "sonnet"), ClaudeCLIBackend)
    assert isinstance(build_backend("mock", "mock-1"), MockBackend)
    with pytest.raises(KeyError):
        build_backend("nope", "x")


def test_backends_round_trip_through_config():
    for provider, model in (("anthropic", "claude-sonnet-5"), ("openai", "gpt-4o"),
                            ("claude_cli", "sonnet"), ("mock", "mock-1")):
        backend = build_backend(provider, model, {"temperature": 0.1, "max_tokens": 128})
        config = backend.to_config()
        rebuilt = build_backend(config["provider"], config["model"], config["params"])
        assert rebuilt.to_config() == config, f"{provider} is not reconstructible"


def test_custom_provider_can_be_registered_and_used(tmp_path):
    register_backend("echo", EchoBackend)
    config = make_project(tmp_path, overrides={"agents": {
        "reviewer3": {"provider": "echo", "model": "echo-1"},
    }})
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.phase1_independent_reviews()

    providers = {row["agent_id"]: row["provider"] for row in store.calls(orchestrator.run_id)}
    assert providers["Reviewer1"] == "mock"
    assert providers["Reviewer3"] == "echo", "per-agent provider override did not take effect"
    echo_message = [row for row in store.all_messages(orchestrator.run_id)
                    if row["sender"] == "Reviewer3"][0]
    assert echo_message["content"].startswith("ECHO:")


def test_mixed_providers_are_recorded_for_reproducibility(tmp_path):
    register_backend("echo", EchoBackend)
    config = make_project(tmp_path, overrides={"agents": {
        "author": {"provider": "echo", "model": "echo-author"},
        "editor": {"provider": "mock", "model": "mock-editor"},
    }})
    model_config = config.model_config()
    assert model_config["Author"]["provider"] == "echo"
    assert model_config["Editor"]["model"] == "mock-editor"
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    from peerreview.util import jload
    stored = jload(store.get_run(orchestrator.run_id)["model_config_json"], {})
    assert stored["Author"]["provider"] == "echo"


class FlakyBackend(AgentBackend):
    provider = "flaky"

    def __init__(self, model="flaky-1", *, failures=2, **kwargs):
        super().__init__(model, retry_base_delay=0.0, **kwargs)
        self.failures = failures
        self.attempts = 0

    def _send(self, messages, system_prompt, tools):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise BackendError("429 rate limited", retryable=True)
        return BackendResponse(text="ok", provider=self.provider, model=self.model)


def test_retries_are_bounded_and_only_for_retryable_errors():
    backend = FlakyBackend(failures=2)
    assert backend.send([ChatMessage("user", "hi")], "sys").text == "ok"
    assert backend.attempts == 3

    exhausted = FlakyBackend(failures=99, max_retries=1)
    with pytest.raises(BackendError):
        exhausted.send([ChatMessage("user", "hi")], "sys")
    assert exhausted.attempts == 2

    class Fatal(FlakyBackend):
        def _send(self, messages, system_prompt, tools):
            self.attempts += 1
            raise BackendError("401 unauthorized", retryable=False)

    fatal = Fatal()
    with pytest.raises(BackendError):
        fatal.send([ChatMessage("user", "hi")], "sys")
    assert fatal.attempts == 1, "non-retryable errors must not be retried"


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend = AnthropicBackend("claude-sonnet-5", max_retries=0)
    with pytest.raises(BackendError, match="ANTHROPIC_API_KEY"):
        backend.send([ChatMessage("user", "hi")], "sys")


def test_agent_failure_does_not_abort_the_run(tmp_path):
    class BrokenBackend(AgentBackend):
        provider = "broken"

        def _send(self, messages, system_prompt, tools):
            raise BackendError("provider exploded", retryable=False)

    register_backend("broken", BrokenBackend)
    config = make_project(tmp_path, overrides={"agents": {
        "reviewer2": {"provider": "broken", "model": "broken-1"}}})
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.phase1_independent_reviews()

    errors = [row for row in store.calls(orchestrator.run_id) if row["error"]]
    assert errors and errors[0]["agent_id"] == "Reviewer2"
    assert store.get_agents(orchestrator.run_id)
    # The other two reviews still landed.
    reviews = [row for row in store.all_messages(orchestrator.run_id)
               if row["message_type"] == "independent_review"]
    assert {row["sender"] for row in reviews} == {"Reviewer1", "Reviewer3"}


class ToolCallingBackend(AgentBackend):
    provider = "tooly"

    def _send(self, messages, system_prompt, tools):
        return BackendResponse(text="ok", provider=self.provider, model=self.model,
                               tool_calls=[{"type": "tool_use", "name": "lookup",
                                            "input": {"q": "Table 3"}}])


def test_tool_calls_reach_the_reproducibility_log(tmp_path):
    from peerreview.util import jload

    register_backend("tooly", ToolCallingBackend)
    config = make_project(tmp_path, overrides={"agents": {
        "reviewer1": {"provider": "tooly", "model": "tooly-1"}}})
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.phase1_independent_reviews()

    call = [row for row in store.calls(orchestrator.run_id) if row["agent_id"] == "Reviewer1"][0]
    assert jload(call["tool_calls_json"], [])[0]["name"] == "lookup"
    assert call["system_prompt"] and call["messages_json"] and call["latency_ms"] is not None
