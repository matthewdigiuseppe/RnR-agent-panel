"""Requirements 1-3: independence in phase 1, full visibility in phase 3,
and reviewer-to-reviewer communication."""
from __future__ import annotations

from peerreview.config import Config
from peerreview.db import Store
from peerreview.models import REVIEWERS
from peerreview.orchestrator import PHASE_REVIEW, Orchestrator
from peerreview.reporting import Reporter
from peerreview.util import jload

from conftest import make_project


def test_independent_reviews_are_invisible_to_other_reviewers(tmp_path):
    config = make_project(tmp_path)
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.phase1_independent_reviews()
    run_id = orchestrator.run_id

    reviews = [row for row in store.all_messages(run_id)
               if row["message_type"] == "independent_review"]
    assert len(reviews) == 3
    assert all(row["visibility"] == "private" for row in reviews)

    for reviewer in REVIEWERS:
        visible = store.visible_messages(run_id, reviewer)
        senders = {row["sender"] for row in visible}
        assert senders <= {reviewer}, f"{reviewer} can see another reviewer's independent review"

    # The Editor is the only recipient at this stage.
    editor_visible = {row["sender"] for row in store.visible_messages(run_id, "Editor")}
    assert editor_visible == set(REVIEWERS)

    # The Author cannot see the reviews before deliberation opens either.
    assert store.visible_messages(run_id, "Author") == []


def test_deliberation_discloses_everything_to_everyone(tmp_path):
    config = make_project(tmp_path)
    store = Store(config.db_path)
    orchestrator = Orchestrator.create(config, store=store, reporter=Reporter(quiet=True))
    orchestrator.phase1_independent_reviews()
    orchestrator.phase2_consolidation()
    orchestrator.open_deliberation()
    run_id = orchestrator.run_id

    review_ids = {row["id"] for row in store.all_messages(run_id)
                  if row["message_type"] == "independent_review"}
    for agent in ("Author", *REVIEWERS, "Editor"):
        visible = {row["id"] for row in store.visible_messages(run_id, agent, as_of_round=1)}
        assert review_ids <= visible, f"{agent} cannot see the independent reviews after opening"

    # History is preserved: disclosure is recorded as a round, not backdated,
    # so replaying the run at round 0 still shows the reviews as unseen.
    for row in store.all_messages(run_id):
        if row["id"] in review_ids:
            assert row["visibility"] == "private"
            assert row["disclosed_at_round"] == 1
    earlier = store.visible_messages(run_id, "Reviewer1", as_of_round=0)
    assert not [r for r in earlier
                if r["message_type"] == "independent_review" and r["sender"] != "Reviewer1"]


def test_reviewers_address_each_other_directly(demo_run):
    store, run_id, _ = demo_run
    reviewer_to_reviewer = []
    for row in store.all_messages(run_id):
        if row["sender"] not in REVIEWERS:
            continue
        recipients = jload(row["recipients"], []) or []
        if any(r in REVIEWERS and r != row["sender"] for r in recipients):
            reviewer_to_reviewer.append(row)
    assert reviewer_to_reviewer, "no reviewer addressed another reviewer"

    # And the challenge actually landed: R2 answered R3 on the same issue.
    senders = {(row["sender"], tuple(jload(row["recipients"], []) or []))
               for row in reviewer_to_reviewer}
    assert any(sender == "Reviewer2" and "Reviewer3" in recipients
               for sender, recipients in senders)
