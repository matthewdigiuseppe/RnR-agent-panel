"""Agents are genuinely separate contexts, and can be separate OS processes."""
from __future__ import annotations

import os

from peerreview.agents import _run_isolated
from peerreview.db import Store
from peerreview.orchestrator import Orchestrator
from peerreview.reporting import Reporter

from conftest import make_project


def test_each_agent_has_its_own_identity_prompt_and_backend(tmp_path):
    config = make_project(tmp_path)
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    agents = orchestrator.pool.agents
    assert set(agents) == {"Author", "Reviewer1", "Reviewer2", "Reviewer3", "Editor"}
    prompts = {agent_id: agent.system_prompt for agent_id, agent in agents.items()}
    assert len(set(prompts.values())) == 5, "agents must not share a system prompt"
    assert len({id(agent.backend) for agent in agents.values()}) == 5
    assert "Methods / Identification reviewer" in prompts["Reviewer2"]
    assert "Generalist / Journal reviewer" in prompts["Reviewer3"]
    assert "chairing this deliberation" in prompts["Editor"]
    assert "not trying to maximize agreement" in prompts["Author"]

    stored = {row["id"]: row["system_prompt"] for row in store.get_agents(orchestrator.run_id)}
    assert stored == prompts, "prompts must be persisted for reproducibility"


def test_process_isolation_runs_phase_one_in_separate_processes(tmp_path):
    config = make_project(tmp_path, overrides={"execution": {"isolation": "process",
                                                            "max_workers": 3}})
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    assert orchestrator.pool.isolation == "process"
    orchestrator.phase1_independent_reviews()

    reviews = [row for row in store.all_messages(orchestrator.run_id)
               if row["message_type"] == "independent_review"]
    assert {row["sender"] for row in reviews} == {"Reviewer1", "Reviewer2", "Reviewer3"}
    assert all("<<<ISSUE>>>" in row["content"] for row in reviews)
    assert len(store.list_issues(orchestrator.run_id, provisional=True)) == 6


def test_isolated_worker_payload_is_self_contained():
    """The worker rebuilds the backend from config alone -- no shared state."""
    payload = {
        "provider": "mock", "model": "mock-1",
        "params": {"script": {"rules": [{"all": ["AGENT: Reviewer1"], "respond": "isolated"}],
                              "default": "fallback"}},
        "system_prompt": "you are reviewer 1",
        "messages": [{"role": "user", "content": "AGENT: Reviewer1"}],
    }
    result = _run_isolated(payload)
    assert result["text"] == "isolated"
    assert result["error"] is None


def test_isolated_worker_returns_errors_instead_of_crashing_the_run():
    result = _run_isolated({"provider": "nonexistent", "model": "x", "params": {},
                            "system_prompt": "s", "messages": [{"role": "user", "content": "c"}]})
    assert result["error"] and "nonexistent" in result["error"]
    assert result["text"] == ""


def test_thread_and_process_isolation_agree(tmp_path):
    outputs = {}
    for isolation in ("serial", "thread", "process"):
        config = make_project(tmp_path / isolation,
                              overrides={"execution": {"isolation": isolation}})
        store = Store(config.db_path)
        orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
        orchestrator.phase1_independent_reviews()
        outputs[isolation] = {
            row["sender"]: row["content"]
            for row in store.all_messages(orchestrator.run_id)
            if row["message_type"] == "independent_review"}
    assert outputs["serial"] == outputs["thread"] == outputs["process"]
