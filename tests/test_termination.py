"""Requirement 10: terminal statuses and stopping rules, without forced consensus."""
from __future__ import annotations

import json

from peerreview.models import (STATUS_UNRESOLVED, TERMINAL_STATUSES, is_terminal)
from peerreview.util import jload

from conftest import DEMO, make_project, run_project


def _reviews_only_script() -> dict:
    """Keep phase 1 and consolidation; everyone is silent during deliberation."""
    script = json.loads((DEMO / "mock_script.json").read_text(encoding="utf-8"))
    keep = [rule for rule in script["rules"]
            if "PHASE: independent_review" in " ".join(rule["all"])
            or "PHASE: consolidation" in " ".join(rule["all"])]
    return {"default": "NO FURTHER COMMENT.", "rules": keep}


def test_stops_when_no_position_changes_for_two_rounds(tmp_path):
    config = make_project(tmp_path, script=_reviews_only_script(),
                          overrides={"deliberation": {"max_rounds": 12,
                                                      "stable_rounds_before_stop": 2}})
    store, run_id, _ = run_project(config)
    run = store.get_run(run_id)
    assert "no substantive position change" in run["termination_reason"]
    assert run["current_round"] == 2, "should stop at the stability threshold, not max_rounds"
    # Nothing was forced into RESOLVED to make the ledger look tidy.
    statuses = {row["status"] for row in store.list_issues(run_id)}
    assert statuses == {"OPEN"}


def test_stops_early_when_every_issue_is_terminal(demo_run):
    store, run_id, _ = demo_run
    run = store.get_run(run_id)
    assert run["current_round"] < run["max_rounds"]
    assert all(is_terminal(row["status"]) for row in store.list_issues(run_id))
    assert run["status"] == "finished"


def test_unresolved_disagreement_is_a_legitimate_terminal_state(demo_run):
    store, run_id, _ = demo_run
    unresolved = [row for row in store.list_issues(run_id)
                  if row["status"] == STATUS_UNRESOLVED]
    assert unresolved, "the scripted run should end with one genuine disagreement"
    assert STATUS_UNRESOLVED in TERMINAL_STATUSES
    issue = unresolved[0]
    assert issue["closed_round"] is not None
    assert issue["required_action"], "an unresolved issue can still carry actionable parts"


def test_no_further_discussion_after_an_issue_closes(demo_run):
    store, run_id, _ = demo_run
    for issue in store.list_issues(run_id):
        closed = issue["closed_round"]
        if closed is None:
            continue
        for message in store.all_messages(run_id):
            if message["round"] <= closed or message["sender"] == "Editor":
                continue
            assert issue["issue_id"] not in (jload(message["issue_ids"], []) or []), (
                f"{message['sender']} discussed {issue['issue_id']} after it closed")


def test_max_rounds_is_respected(tmp_path):
    config = make_project(tmp_path, script=_reviews_only_script(),
                          overrides={"deliberation": {"max_rounds": 1,
                                                      "stable_rounds_before_stop": 99}})
    store, run_id, _ = run_project(config)
    run = store.get_run(run_id)
    assert run["termination_reason"] == "max_rounds (1) reached"
    assert run["current_round"] == 1
