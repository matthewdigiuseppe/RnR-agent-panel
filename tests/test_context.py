"""Context assembly: budgets, prioritization, and no re-sending the world."""
from __future__ import annotations

from peerreview.context import ContextBuilder, render_message
from peerreview.db import Store
from peerreview.manuscript import load_bundle
from peerreview.models import STATUS_RESOLVED
from peerreview.util import estimate_tokens

from conftest import make_project


def _builder(tmp_path, **kwargs):
    config = make_project(tmp_path)
    store = Store(config.db_path)
    store.create_run(run_id="r", project="p", manuscript="m", manuscript_hash="h", inputs={},
                     journal=None, config={}, model_config={}, max_rounds=5)
    inputs = config.manuscript_inputs()
    bundle = load_bundle(manuscript=inputs["manuscript"], appendix=inputs["appendix"])
    return store, ContextBuilder(store, "r", bundle, **kwargs)


def test_prompt_respects_the_token_budget(tmp_path):
    store, builder = _builder(tmp_path, max_prompt_tokens=1200)
    messages = builder.build(agent_id="Reviewer1", role="reviewer", phase="independent_review",
                             round=0, directive="Review the manuscript.")
    body = messages[-1].content
    assert estimate_tokens(body) <= 1400, "budget overrun"
    assert "YOUR INSTRUCTION THIS TURN" in body, "the instruction is never dropped"
    assert "AGENT: Reviewer1" in body


def test_turn_header_identifies_the_agent_and_issues(tmp_path):
    store, builder = _builder(tmp_path)
    store.create_issue("r", issue_id="ISSUE-001", title="Pre-trends", actor="Editor", round=0,
                       manuscript_location="Section 3")
    messages = builder.build(agent_id="Reviewer2", role="reviewer", phase="deliberation", round=3,
                             directive="Answer the chair.", focus_issue_ids=["ISSUE-001"])
    header = messages[-1].content
    assert "AGENT: Reviewer2" in header
    assert "PHASE: deliberation" in header
    assert "ROUND: 3" in header
    assert "ISSUES ON THE TABLE: ISSUE-001" in header


def test_settled_issues_appear_as_summaries_not_transcripts(tmp_path):
    store, builder = _builder(tmp_path)
    store.create_issue("r", issue_id="ISSUE-001", title="Open thing", actor="Editor", round=0)
    store.create_issue("r", issue_id="ISSUE-002", title="Settled thing", actor="Editor", round=0)
    store.update_issue("r", "ISSUE-002", actor="Editor", round=1, status=STATUS_RESOLVED,
                       required_action="Add a footnote.")
    for index in range(6):
        store.add_message(run_id="r", round=1, phase="deliberation", sender="Reviewer1",
                          recipients=["ALL"], content=f"long argument about the settled thing {index} "
                          + "detail " * 60, message_type="deliberation", issue_ids=["ISSUE-002"])
    messages = builder.build(agent_id="Reviewer3", role="reviewer", phase="deliberation", round=2,
                             directive="Discuss.", focus_issue_ids=["ISSUE-001"])
    body = messages[-1].content
    assert "SETTLED (do not reopen without new evidence)" in body
    assert "Add a footnote." in body
    assert body.count("long argument about the settled thing") <= 1, (
        "settled-issue transcripts must not be replayed in full")


def test_manuscript_excerpts_track_the_issue_under_discussion(tmp_path):
    store, builder = _builder(tmp_path)
    store.create_issue("r", issue_id="ISSUE-001", title="Parallel trends is asserted",
                       actor="Editor", round=0, manuscript_location="Section 3",
                       description="two-way fixed effects, pre-trends, event study")
    messages = builder.build(agent_id="Reviewer2", role="reviewer", phase="deliberation", round=1,
                             directive="Assess.", focus_issue_ids=["ISSUE-001"])
    body = messages[-1].content
    assert "MANUSCRIPT EXCERPTS" in body
    assert "difference-in-differences" in body or "fixed effects" in body
    assert "MANUSCRIPT MAP" in body, "the outline is always available for citation"


def test_agent_sees_its_own_previous_turns(tmp_path):
    store, builder = _builder(tmp_path, own_turn_memory=2)
    for round_number in (1, 2, 3):
        store.add_message(run_id="r", round=round_number, phase="deliberation", sender="Reviewer1",
                          recipients=["ALL"], content=f"my turn in round {round_number}",
                          message_type="deliberation")
    messages = builder.build(agent_id="Reviewer1", role="reviewer", phase="deliberation", round=4,
                             directive="Continue.")
    assistant_turns = [m.content for m in messages if m.role == "assistant"]
    assert assistant_turns == ["my turn in round 2", "my turn in round 3"]


def test_messages_are_rendered_with_citable_ids(tmp_path):
    store, _ = _builder(tmp_path)
    message_id = store.add_message(run_id="r", round=2, phase="deliberation", sender="Reviewer2",
                                   recipients=["Reviewer3"], content="estimand point",
                                   message_type="deliberation", issue_ids=["ISSUE-002"])
    rendered = render_message(store.get_message("r", message_id))
    assert rendered.startswith(f"[M{message_id}] Reviewer2 -> Reviewer3 (round 2, ISSUE-002)")
