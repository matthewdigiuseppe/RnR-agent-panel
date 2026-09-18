"""Requirements 5 and 6: position changes are logged, and every influence link
points at a real message."""
from __future__ import annotations

from peerreview.db import Store
from peerreview.influence import collect_edges, influence_report, invalid_edges, issue_influence
from peerreview.ledger import record_position_changes

CHANGE_BLOCK = """@Reviewer2 is right about the estimand.

<<<POSITION CHANGE>>>
ISSUE: ISSUE-001
PREVIOUS POSITION: NEW ANALYSIS REQUIRED
NEW POSITION: CLARIFICATION REQUIRED
REASON: The proposed estimator targets a different quantity.
TRIGGERED BY: M{mid} (@Reviewer2)
REMAINING CONCERN: the prose still overstates the mechanism
<<<END POSITION CHANGE>>>"""


def _seed(store: Store) -> tuple[str, int]:
    store.create_run(run_id="r", project="p", manuscript="m.md", manuscript_hash="h", inputs={},
                     journal=None, config={}, model_config={}, max_rounds=5)
    store.create_issue("r", issue_id="ISSUE-001", title="Estimand", actor="Editor", round=0)
    trigger = store.add_message(run_id="r", round=1, phase="deliberation", sender="Reviewer2",
                                recipients=["ALL"], content="estimand argument",
                                message_type="deliberation", issue_ids=["ISSUE-001"])
    return "r", trigger


def test_position_change_is_recorded_with_its_trigger(tmp_path):
    store = Store(tmp_path / "t.db")
    run_id, trigger = _seed(store)
    message_id = store.add_message(run_id=run_id, round=2, phase="deliberation", sender="Reviewer1",
                                   recipients=["ALL"], content=CHANGE_BLOCK.format(mid=trigger),
                                   message_type="deliberation", issue_ids=["ISSUE-001"])
    events = record_position_changes(store, run_id, round=2, agent_id="Reviewer1",
                                     text=CHANGE_BLOCK.format(mid=trigger), message_id=message_id)
    assert len(events) == 1

    positions = store.positions(run_id, "ISSUE-001", "Reviewer1")
    assert len(positions) == 1
    position = positions[0]
    assert position["changed_from"] == "NEW ANALYSIS REQUIRED"
    assert position["position"] == "CLARIFICATION REQUIRED"
    assert position["triggered_by_message_id"] == trigger
    assert position["triggered_by_agent"] == "Reviewer2"
    assert "overstates" in position["remaining_concern"]
    assert store.latest_position(run_id, "ISSUE-001", "Reviewer1") == "CLARIFICATION REQUIRED"


def test_unknown_trigger_falls_back_to_a_real_message(tmp_path):
    store = Store(tmp_path / "t.db")
    run_id, trigger = _seed(store)
    text = CHANGE_BLOCK.format(mid=98765)          # a message id that does not exist
    message_id = store.add_message(run_id=run_id, round=2, phase="deliberation", sender="Reviewer1",
                                   recipients=["ALL"], content=text, message_type="deliberation",
                                   issue_ids=["ISSUE-001"])
    record_position_changes(store, run_id, round=2, agent_id="Reviewer1", text=text,
                            message_id=message_id)
    position = store.positions(run_id, "ISSUE-001", "Reviewer1")[0]
    assert position["triggered_by_message_id"] == trigger, "should resolve via the cited agent"
    assert invalid_edges(store, run_id) == []


def test_trigger_that_cannot_be_resolved_is_dropped_not_invented(tmp_path):
    store = Store(tmp_path / "t.db")
    run_id, _ = _seed(store)
    text = """<<<POSITION CHANGE>>>
ISSUE: ISSUE-001
PREVIOUS POSITION: OBJECTION
NEW POSITION: WITHDRAWN
REASON: on reflection
TRIGGERED BY: nobody in particular
<<<END POSITION CHANGE>>>"""
    message_id = store.add_message(run_id=run_id, round=2, phase="deliberation", sender="Reviewer3",
                                   recipients=["ALL"], content=text, message_type="deliberation")
    record_position_changes(store, run_id, round=2, agent_id="Reviewer3", text=text,
                            message_id=message_id)
    position = store.positions(run_id, "ISSUE-001", "Reviewer3")[0]
    assert position["triggered_by_message_id"] is None
    assert invalid_edges(store, run_id) == []


def test_influence_links_in_a_full_run_are_all_valid(demo_run):
    store, run_id, _ = demo_run
    report = influence_report(store, run_id)
    assert report["invalid_edges"] == []
    assert report["total_position_changes"] >= 3

    for edge in collect_edges(store, run_id):
        if edge.message_id is None:
            continue
        message = store.get_message(run_id, edge.message_id)
        assert message is not None
        assert message["sender"] == edge.source
        # A trigger must precede the change it caused.
        assert message["round"] <= edge.round


def test_influence_path_reconstructs_reviewer_to_reviewer_persuasion(demo_run):
    store, run_id, _ = demo_run
    influence = issue_influence(store, run_id, "ISSUE-002")
    assert influence.path[:3] == ["Reviewer2", "Reviewer3", "Author"]
    assert influence.path[-1] == "Editor decision"
    edge = influence.edges[0]
    assert edge.source == "Reviewer2" and edge.target == "Reviewer3"
    assert "sequential ignorability" in edge.rationale
