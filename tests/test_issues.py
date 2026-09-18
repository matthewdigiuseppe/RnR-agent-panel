"""Requirements 4 and 7: merging preserves original comments; closed issues
cannot be reopened without a reason."""
from __future__ import annotations

import pytest

from peerreview.db import ReopenWithoutReason, Store
from peerreview.ledger import apply_editor_moves
from peerreview.models import STATUS_CLOSED, STATUS_OPEN, STATUS_RESOLVED
from peerreview.parsing import parse_editor_moves
from peerreview.util import jload


def test_merge_preserves_original_reviewer_comments(demo_run):
    store, run_id, _ = demo_run
    provisional = store.list_issues(run_id, provisional=True, include_merged=True)
    assert provisional, "expected provisional issues from phase 1"

    for issue in provisional:
        target_id = issue["merged_into"]
        assert target_id, f"{issue['issue_id']} was never consolidated"
        # The provisional row still holds the reviewer's verbatim block.
        assert "<<<ISSUE>>>" in issue["original_comment"]
        assert issue["title"]
        target = store.get_issue(run_id, target_id)
        # And the canonical issue carries that text forward.
        assert issue["original_comment"].strip() in target["original_comment"]
        # Attribution survives the merge.
        raised = jload(target["raised_by"], []) or []
        for agent in jload(issue["raised_by"], []) or []:
            assert agent in raised


def test_merge_records_an_audit_event(demo_run):
    store, run_id, _ = demo_run
    merged = [row for row in store.list_issues(run_id, provisional=True, include_merged=True)
              if row["merged_into"]]
    issue = merged[0]
    events = [e for e in store.issue_events(run_id, issue["issue_id"]) if e["event_type"] == "merge"]
    assert events, "merge produced no audit event"
    assert jload(events[0]["payload_json"], {})["merged_into"] == issue["merged_into"]


def test_closed_issue_cannot_be_reopened_without_a_reason(tmp_path):
    store = Store(tmp_path / "t.db")
    store.create_run(run_id="r", project="p", manuscript="m.md", manuscript_hash="h", inputs={},
                     journal=None, config={}, model_config={}, max_rounds=5)
    store.create_issue("r", issue_id="ISSUE-001", title="t", actor="Editor", round=0)
    store.update_issue("r", "ISSUE-001", actor="Editor", round=1, status=STATUS_RESOLVED)

    with pytest.raises(ReopenWithoutReason):
        store.update_issue("r", "ISSUE-001", actor="Editor", round=2, status=STATUS_OPEN)
    with pytest.raises(ReopenWithoutReason):
        store.update_issue("r", "ISSUE-001", actor="Editor", round=2, status=STATUS_OPEN,
                           reason="   ", allow_reopen=True)
    assert store.get_issue("r", "ISSUE-001")["status"] == STATUS_RESOLVED

    store.update_issue("r", "ISSUE-001", actor="Editor", round=2, status=STATUS_OPEN,
                       allow_reopen=True, reason="new evidence: Table A9 contradicts the claim")
    issue = store.get_issue("r", "ISSUE-001")
    assert issue["status"] == STATUS_OPEN
    reopen_events = [e for e in store.issue_events("r", "ISSUE-001") if e["event_type"] == "reopen"]
    assert reopen_events and "Table A9" in reopen_events[0]["reason"]


def test_editor_reopen_block_without_reason_is_rejected(tmp_path):
    store = Store(tmp_path / "t.db")
    store.create_run(run_id="r", project="p", manuscript="m.md", manuscript_hash="h", inputs={},
                     journal=None, config={}, model_config={}, max_rounds=5)
    store.create_issue("r", issue_id="ISSUE-001", title="t", actor="Editor", round=0)
    store.update_issue("r", "ISSUE-001", actor="Editor", round=1, status=STATUS_CLOSED)
    message_id = store.add_message(run_id="r", round=2, phase="deliberation", sender="Editor",
                                   recipients=["ALL"], content="x", message_type="ledger")

    moves = parse_editor_moves("<<<REOPEN>>>\nISSUE: ISSUE-001\n<<<END REOPEN>>>")
    events = apply_editor_moves(store, "r", round=2, moves=moves, message_id=message_id)
    assert any(event.kind == "reopen_rejected" for event in events)
    assert store.get_issue("r", "ISSUE-001")["status"] == STATUS_CLOSED

    moves = parse_editor_moves(
        "<<<REOPEN>>>\nISSUE: ISSUE-001\nREASON: Reviewer 2 produced a new counterexample.\n"
        "<<<END REOPEN>>>")
    events = apply_editor_moves(store, "r", round=2, moves=moves, message_id=message_id)
    assert any(event.kind == "reopen" for event in events)
    assert store.get_issue("r", "ISSUE-001")["status"] == STATUS_OPEN


def test_status_and_severity_are_independent(demo_run):
    store, run_id, _ = demo_run
    for issue in store.list_issues(run_id):
        assert issue["severity"] in {"major", "moderate", "minor", None}
        assert issue["status"] not in {"major", "moderate", "minor"}
