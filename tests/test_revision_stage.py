"""The optional final stage: revised manuscript + response letter, with a change
log that refuses to make silent changes."""
from __future__ import annotations

from peerreview.db import Store
from peerreview.exports import build_revision_plan, export_all
from peerreview.orchestrator import Orchestrator
from peerreview.reporting import Reporter
from peerreview.revision import _change_log, run_revision_stage

from conftest import make_project


def test_revision_stage_writes_both_documents(tmp_path):
    config = make_project(tmp_path, overrides={"revision_stage": {"enabled": True}})
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.run()
    out_dir = tmp_path / "out"
    export_all(store, orchestrator.run_id, out_dir)
    written = run_revision_stage(orchestrator, out_dir)

    manuscript = written["revised_manuscript"].read_text(encoding="utf-8")
    letter = written["response_letter"].read_text(encoding="utf-8")
    assert "<!-- rev: ISSUE-002 -->" in manuscript
    assert "## Change log" in manuscript
    assert "ISSUE-001" in manuscript and "ISSUE-003" in manuscript
    assert "PLACEHOLDER" in manuscript, "unrun analyses must be flagged, not fabricated"
    assert "ISSUE-004" in letter and "unresolved disagreement" in letter.lower()

    # The stage is logged like any other agent turn.
    revision_calls = [row for row in store.calls(orchestrator.run_id) if row["phase"] == "revision"]
    assert len(revision_calls) == 2
    assert all(row["agent_id"] == "Author" for row in revision_calls)


def test_change_log_flags_changes_outside_the_plan(tmp_path):
    plan = [{"issue_id": "ISSUE-001", "title": "In the plan", "classification": "exposition",
             "required_action": "do the thing", "dismissed": False, "status": "EXPOSITION ONLY"},
            {"issue_id": "ISSUE-002", "title": "Also planned", "classification": "clarification",
             "required_action": "fix wording", "dismissed": False,
             "status": "CLARIFICATION REQUIRED"}]
    revised = "Some text <!-- rev: ISSUE-001 --> and a rogue change <!-- rev: ISSUE-099 -->"
    log = _change_log(revised, plan)
    assert "ISSUE-099" in log and "NOT in the revision plan" in log
    assert "Plan items not marked in the revised text" in log
    assert "ISSUE-002" in log.split("Plan items not marked")[1]


def test_disabled_by_default(tmp_path):
    config = make_project(tmp_path)
    assert config.revision_stage_enabled is False
