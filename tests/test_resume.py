"""Requirement 8: runs resume correctly after interruption."""
from __future__ import annotations

import pytest

from peerreview.db import Store
from peerreview.orchestrator import Orchestrator
from peerreview.reporting import Reporter

from conftest import make_project


def test_resume_continues_without_redoing_completed_phases(tmp_path):
    config = make_project(tmp_path)
    store = Store(config.db_path)

    first = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    run_id = first.run_id
    first.phase1_independent_reviews()
    first.phase2_consolidation()
    reviews_before = [m for m in store.all_messages(run_id)
                      if m["message_type"] == "independent_review"]
    issues_before = {row["issue_id"] for row in store.list_issues(run_id)}
    calls_before = len(store.calls(run_id))
    del first  # simulate the process dying

    resumed = Orchestrator.resume(config, run_id, store=store, reporter=Reporter(quiet=True))
    assert resumed.run_id == run_id
    assert [a.id for a in resumed.pool.agents.values()]
    resumed.run()

    reviews_after = [m for m in store.all_messages(run_id)
                     if m["message_type"] == "independent_review"]
    assert len(reviews_after) == len(reviews_before), "phase 1 was re-run on resume"
    assert issues_before <= {row["issue_id"] for row in store.list_issues(run_id)}
    assert len(store.calls(run_id)) > calls_before, "resume made no progress"
    assert store.get_run(run_id)["status"] == "finished"
    assert store.get_run(run_id)["termination_reason"]


def test_resume_refuses_a_changed_manuscript(tmp_path):
    config = make_project(tmp_path)
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.phase1_independent_reviews()

    manuscript = config.manuscript_inputs()["manuscript"]
    manuscript.write_text(manuscript.read_text() + "\n\nNew section added mid-run.\n")

    with pytest.raises(ValueError, match="manuscript has changed"):
        Orchestrator.resume(config, orchestrator.run_id, store=store, reporter=Reporter(quiet=True))


def test_state_is_fully_reconstructible_from_sqlite(tmp_path):
    """Nothing the run needs lives only in memory."""
    config = make_project(tmp_path)
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.run()
    run_id = orchestrator.run_id
    store.close()

    fresh = Store(config.db_path)
    run = fresh.get_run(run_id)
    assert run["config_json"] and run["model_config_json"]
    assert run["manuscript_hash"]
    assert len(fresh.get_agents(run_id)) == 5
    assert all(row["system_prompt"] for row in fresh.get_agents(run_id))
    calls = fresh.calls(run_id)
    assert calls and all(row["system_prompt"] and row["messages_json"] for row in calls)
    assert all(row["provider"] and row["model"] for row in calls)
