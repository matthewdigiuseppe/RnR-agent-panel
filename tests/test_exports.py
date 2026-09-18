"""Requirement 12: the revision plan contains every non-dismissed issue, plus
the shape of the other outputs."""
from __future__ import annotations

import json

from peerreview.exports import (FILES, build_results, build_revision_plan, export_all,
                                ledger_snapshot, run_directory)
from peerreview.models import DISMISSED_STATUSES, STATUS_UNRESOLVED


def test_revision_plan_contains_every_issue(demo_run, tmp_path):
    store, run_id, config = demo_run
    written = export_all(store, run_id, tmp_path / "out")
    plan_text = written["revision_plan"].read_text(encoding="utf-8")

    issues = store.list_issues(run_id)
    assert issues
    for issue in issues:
        assert issue["issue_id"] in plan_text, f"{issue['issue_id']} missing from the plan"
        assert issue["title"] in plan_text

    non_dismissed = [row for row in issues if row["status"] not in DISMISSED_STATUSES]
    plan = build_revision_plan(store, run_id)
    planned_ids = {item["issue_id"] for item in plan}
    assert {row["issue_id"] for row in non_dismissed} <= planned_ids
    assert len(plan) == len(issues), "issues must appear exactly once"


def test_revision_plan_items_are_operational(demo_run):
    store, run_id, _ = demo_run
    for item in build_revision_plan(store, run_id):
        assert item["priority"] in {"essential", "important", "minor"}
        assert item["classification"]
        assert item["analysis_required"] in {"yes", "no", "optional"}
        assert item["textual_revision_required"] in {"yes", "no"}
        assert set(item["reviewer_positions"]) == {"Reviewer1", "Reviewer2", "Reviewer3"}
        assert item["influence_path"]
        if not item["dismissed"]:
            assert item["required_action"], f"{item['issue_id']} has no required action"

    analysis_items = [i for i in build_revision_plan(store, run_id)
                      if i["analysis_required"] == "yes"]
    assert analysis_items, "the scripted run requires one new analysis"
    assert all(item["analysis_detail"] for item in analysis_items)


def test_all_eight_outputs_are_written(demo_run, tmp_path):
    store, run_id, _ = demo_run
    written = export_all(store, run_id, tmp_path / "out")
    for key in ("initial_reviews", "ledger_initial", "transcript", "ledger_final",
                "revision_plan", "unresolved", "influence", "json"):
        path = written[key]
        assert path.exists() and path.stat().st_size > 0
        assert path.name == FILES[key]


def test_unresolved_output_records_the_disagreement(demo_run, tmp_path):
    store, run_id, _ = demo_run
    written = export_all(store, run_id, tmp_path / "out")
    text = written["unresolved"].read_text(encoding="utf-8")
    unresolved = [row for row in store.list_issues(run_id) if row["status"] == STATUS_UNRESOLVED]
    for issue in unresolved:
        assert issue["issue_id"] in text
    assert "did not force consensus" in text


def test_initial_ledger_is_a_snapshot_not_the_final_state(demo_run, tmp_path):
    store, run_id, _ = demo_run
    snapshot = ledger_snapshot(store, run_id, as_of_round=0)
    assert snapshot
    assert all(issue["status"] == "OPEN" for issue in snapshot), (
        "the initial ledger must show issues as they stood before deliberation")
    final = {row["issue_id"]: row["status"] for row in store.list_issues(run_id)}
    assert any(status != "OPEN" for status in final.values())


def test_initial_ledger_rolls_back_later_field_changes(demo_run):
    """Fields edited during deliberation must show their pre-deliberation values."""
    store, run_id, _ = demo_run
    snapshot = {issue["issue_id"]: issue for issue in ledger_snapshot(store, run_id, as_of_round=0)}
    final = {row["issue_id"]: row for row in store.list_issues(run_id)}
    changed = [issue_id for issue_id in snapshot
               if snapshot[issue_id]["severity"] != final[issue_id]["severity"]]
    assert changed, "the scripted run downgrades at least one severity during deliberation"
    for issue_id in changed:
        assert snapshot[issue_id]["severity"] in {"major", "moderate", "minor"}
    assert all(not issue["required_action"] or "Answer:" in issue["required_action"]
               for issue in snapshot.values()), (
        "the initial ledger carries the editor's question, not later required actions")


def test_machine_readable_results_round_trip(demo_run, tmp_path):
    store, run_id, _ = demo_run
    written = export_all(store, run_id, tmp_path / "out")
    data = json.loads(written["json"].read_text(encoding="utf-8"))
    assert data["run"]["run_id"] == run_id
    assert data["run"]["manuscript_hash"]
    assert data["run"]["config"] and data["run"]["model_config"]
    assert len(data["agents"]) == 5
    assert data["messages"] and data["issues"] and data["positions"]
    assert data["revision_plan"] and data["influence"]["issues"]
    assert data["usage"]["calls"] > 0
    # Provisional reviewer issues are retained in the machine-readable output.
    assert any(issue["provisional"] for issue in data["issues"])


def test_run_directory_is_stable_per_run(demo_run, tmp_path):
    store, run_id, _ = demo_run
    run = store.get_run(run_id)
    first = run_directory(tmp_path, run)
    second = run_directory(tmp_path, run)
    assert first == second
    assert first.name.startswith(run["started_at"][:10])
