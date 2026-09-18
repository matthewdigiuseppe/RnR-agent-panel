"""Requirement 9: human interventions are preserved and never overwrite history."""
from __future__ import annotations

from peerreview.db import Store
from peerreview.models import STATUS_OPEN, STATUS_RESOLVED
from peerreview.orchestrator import Orchestrator
from peerreview.reporting import Reporter
from peerreview.util import jload

from conftest import make_project


def _prepare(tmp_path):
    config = make_project(tmp_path)
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.phase1_independent_reviews()
    orchestrator.phase2_consolidation()
    return config, store, orchestrator


def test_human_message_is_stored_distinctly_from_agent_messages(tmp_path):
    config, store, orchestrator = _prepare(tmp_path)
    run_id = orchestrator.run_id
    store.add_intervention(run_id, round=1, kind="message", speak_as="Author",
                           issue_id="ISSUE-001",
                           payload={"text": "The event study is in Appendix A1, Figure A1."})
    orchestrator._apply_interventions(1)

    human = [row for row in store.all_messages(run_id) if row["is_human"]]
    assert len(human) == 1
    message = human[0]
    assert message["sender"] == "HUMAN(Author)"
    assert message["message_type"] == "human_intervention"
    assert "Figure A1" in message["content"]
    assert "ISSUE-001" in (jload(message["issue_ids"], []) or [])

    interventions = store.all_interventions(run_id)
    assert len(interventions) == 1
    assert interventions[0]["consumed"] == 1
    assert interventions[0]["message_id"] == message["id"]

    # Agents see it in the shared room.
    visible = store.visible_messages(run_id, "Reviewer2", as_of_round=1)
    assert message["id"] in {row["id"] for row in visible}


def test_human_correction_and_evidence_keep_their_kind(tmp_path):
    config, store, orchestrator = _prepare(tmp_path)
    run_id = orchestrator.run_id
    store.add_intervention(run_id, round=1, kind="correction", speak_as="Editor",
                           payload={"text": "Reviewer3 misread Table 3; it is a tercile split."})
    store.add_intervention(run_id, round=1, kind="evidence", speak_as="Author",
                           payload={"text": "Adding the pre-registration timestamp."})
    orchestrator._apply_interventions(1)
    kinds = {row["kind"] for row in store.all_interventions(run_id)}
    assert kinds == {"correction", "evidence"}
    contents = " ".join(row["content"] for row in store.all_messages(run_id) if row["is_human"])
    assert "HUMAN CORRECTION" in contents and "HUMAN EVIDENCE" in contents


def test_human_can_reopen_an_issue_with_a_reason(tmp_path):
    config, store, orchestrator = _prepare(tmp_path)
    run_id = orchestrator.run_id
    store.update_issue(run_id, "ISSUE-001", actor="Editor", round=1, status=STATUS_RESOLVED)
    store.add_intervention(run_id, round=2, kind="reopen", issue_id="ISSUE-001",
                           payload={"reason": "I have new data the panel has not seen."})
    orchestrator._apply_interventions(2)

    assert store.get_issue(run_id, "ISSUE-001")["status"] == STATUS_OPEN
    events = [e for e in store.issue_events(run_id, "ISSUE-001") if e["event_type"] == "reopen"]
    assert events and "new data" in events[0]["reason"]
    # The prior status change is still in the audit trail: history is not rewritten.
    statuses = [e["event_type"] for e in store.issue_events(run_id, "ISSUE-001")]
    assert statuses.count("status_change") >= 1


def test_focus_and_pause_are_honoured(tmp_path):
    config, store, orchestrator = _prepare(tmp_path)
    run_id = orchestrator.run_id
    store.add_intervention(run_id, round=1, kind="focus",
                           payload={"issue_ids": ["ISSUE-003", "ISSUE-999"]})
    orchestrator._apply_interventions(1)
    assert orchestrator.forced_focus == ["ISSUE-003"], "unknown ids must be dropped"

    store.add_intervention(run_id, round=1, kind="pause")
    orchestrator._apply_interventions(1)
    assert store.run_status(run_id) == "paused"

    reason = orchestrator.phase3_deliberation()
    assert "paused" in reason
    assert store.get_run(run_id)["status"] == "paused"


def test_interventions_survive_export(tmp_path):
    from peerreview.exports import build_results

    config, store, orchestrator = _prepare(tmp_path)
    run_id = orchestrator.run_id
    store.add_intervention(run_id, round=1, kind="message", speak_as="Author",
                           payload={"text": "Please focus on identification."})
    orchestrator._apply_interventions(1)
    results = build_results(store, run_id)
    assert results["interventions"] and results["interventions"][0]["kind"] == "message"
    assert any(message["is_human"] for message in results["messages"])
