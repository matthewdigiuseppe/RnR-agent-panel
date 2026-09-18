"""Structured-output parsing: forgiving of model sloppiness, strict about meaning."""
from __future__ import annotations

from peerreview.models import STATUS_CLARIFICATION, STATUS_EXPOSITION, normalize_status
from peerreview.parsing import (extract_issue_ids, extract_mentions, normalize_issue_id,
                                parse_editor_moves, parse_issues, parse_position_changes,
                                parse_revisions, resolve_recipients, strip_blocks)


def test_issue_block_parsing_is_tolerant_of_key_variants():
    text = """<<<ISSUE>>>
**TITLE**: Pre-trends not shown
Category: identification
SEVERITY : major
CLAIM: The manuscript asserts parallel trends.
WHY THIS MATTERS: The ATT is not interpretable otherwise.
LOCATION: Section 3, p. 9
PROPOSED REMEDY: Report the event study.
WHAT WOULD RESOLVE THIS: Flat pre-trends.
CONFIDENCE: high
"""
    issue = parse_issues(text)[0]
    assert issue.title == "Pre-trends not shown"
    assert issue.category == "identification"
    assert issue.severity == "major"
    assert issue.location == "Section 3, p. 9"
    assert issue.confidence == "high"
    assert "WHY IT MATTERS" in issue.description


def test_multiline_values_are_preserved():
    text = """<<<ISSUE>>>
ISSUE TITLE: A
CLAIM: line one
  line two
  line three
SEVERITY: minor
<<<END ISSUE>>>"""
    issue = parse_issues(text)[0]
    assert issue.claim.splitlines() == ["line one", "line two", "line three"]


def test_issue_id_normalization():
    assert normalize_issue_id("ISSUE 7") == "ISSUE-007"
    assert normalize_issue_id("issue-007") == "ISSUE-007"
    assert normalize_issue_id("ISSUE-7") == "ISSUE-007"
    assert normalize_issue_id("R2-01") == "R2-01"
    assert normalize_issue_id("nothing here") is None
    assert extract_issue_ids("both ISSUE-003 and issue 12 matter") == ["ISSUE-003", "ISSUE-012"]


def test_mentions_and_recipients():
    assert extract_mentions("@Reviewer2 and @Author disagree") == ["Reviewer2", "Author"]
    assert extract_mentions("@Reviewer 3 wrote") == ["Reviewer3"]
    assert resolve_recipients("@ALL please note", ["Editor"]) == ["ALL"]
    assert resolve_recipients("no mentions", ["Editor"]) == ["Editor"]


def test_position_change_parsing_and_status_normalization():
    text = """<<<POSITION CHANGE>>>
ISSUE: ISSUE-014
PREVIOUS POSITION: NEW ANALYSIS REQUIRED
NEW POSITION: clarification only
REASON: R2's estimand argument.
TRIGGERED BY: M42 (@Reviewer2)
REMAINING CONCERN: none
<<<END POSITION CHANGE>>>"""
    change = parse_position_changes(text)[0]
    assert change.issue_id == "ISSUE-014"
    assert change.triggered_by_message_id == 42
    assert change.triggered_by_agent == "Reviewer2"
    assert normalize_status(change.new) == STATUS_CLARIFICATION
    assert normalize_status("EXPOSITION ONLY") == STATUS_EXPOSITION
    assert normalize_status("objection withdrawn") == "REVIEWER WITHDREW OBJECTION"
    assert normalize_status("something else entirely") is None


def test_editor_moves_cover_every_authority():
    text = """<<<AGENDA>>>
ISSUES: ISSUE-003, ISSUE 7
SPEAKERS: @Reviewer2, Author
QUESTION: Does the test target the same estimand?
<<<END AGENDA>>>
<<<NEW ISSUE>>>
TITLE: Attrition is not reported
CATEGORY: research design
SEVERITY: moderate
RAISED BY: R1, R3
<<<END NEW ISSUE>>>
<<<MERGE>>>
SOURCES: R1-02, R3-01
INTO: Attrition is not reported
<<<END MERGE>>>
<<<SPLIT>>>
ISSUE: ISSUE-005
INTO: Measurement of the mediator; Interpretation of Table 3
REASON: two distinct problems
<<<END SPLIT>>>
<<<LEDGER UPDATE>>>
ISSUE: ISSUE-003
STATUS: exposition only
PRIORITY: essential
REQUIRED ACTION: Move Figure A1 into Section 3.
<<<END LEDGER UPDATE>>>
<<<TERMINATE>>>
REASON: repetitive
<<<END TERMINATE>>>"""
    moves = parse_editor_moves(text)
    assert moves.agenda[0]["issue_ids"] == ["ISSUE-003", "ISSUE-007"]
    assert moves.agenda[0]["speakers"] == ["Reviewer2", "Author"]
    assert moves.new_issues[0]["raised_by"] == ["R1", "R3"]
    assert [m["source"] for m in moves.merges] == ["R1-02", "R3-01"]
    assert moves.merges[0]["target"] == "Attrition is not reported"
    assert moves.splits[0]["titles"] == ["Measurement of the mediator", "Interpretation of Table 3"]
    assert moves.updates[0].status == STATUS_EXPOSITION
    assert moves.updates[0].priority == "essential"
    assert moves.terminate == "repetitive"


def test_author_revision_blocks():
    text = """<<<REVISION>>>
ISSUE: ISSUE-002
TYPE: clarification
MANUSCRIPT LOCATION: Abstract sentence 4
ANALYSIS REQUIRED: none
TEXTUAL CHANGE: Replace "operates through" with "is consistent with".
SUBSTANTIVE CLAIMS CHANGE: no
<<<END REVISION>>>"""
    revision = parse_revisions(text)[0]
    assert revision.issue_id == "ISSUE-002"
    assert revision.kind == "clarification"
    assert "consistent with" in revision.change


def test_strip_blocks_leaves_prose_for_word_counting():
    text = "Prose here.\n<<<ISSUE>>>\nTITLE: x\n<<<END ISSUE>>>\nMore prose."
    assert strip_blocks(text) == "Prose here.\n\nMore prose."
