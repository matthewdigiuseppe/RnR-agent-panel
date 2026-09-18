"""Parsers for the structured blocks agents emit.

Agents write ordinary prose plus delimited blocks:

    <<<POSITION CHANGE>>>
    ISSUE: ISSUE-007
    PREVIOUS POSITION: NEW ANALYSIS REQUIRED
    NEW POSITION: CLARIFICATION REQUIRED
    REASON: R2 showed the proposed estimator targets a different estimand.
    TRIGGERED BY: M42 (@Reviewer2)
    REMAINING CONCERN: the prose still overstates the mechanism.
    <<<END POSITION CHANGE>>>

Parsing is deliberately forgiving (missing END markers, key aliases, stray
markdown) because model output is not a wire protocol.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import (ALL_AGENTS, CATEGORIES, CONFIDENCES, SEVERITIES, normalize_enum,
                     normalize_status)

BLOCK_RE = re.compile(r"<<<\s*([A-Z][A-Z _-]*?)\s*>>>(.*?)(?=<<<|\Z)", re.S)
END_RE = re.compile(r"<<<\s*END[ _-][A-Z][A-Z _-]*\s*>>>\s*$", re.S | re.I)
KEY_RE = re.compile(r"^\s{0,3}(?:[-*]\s*)?\*{0,2}([A-Za-z][A-Za-z _/-]{1,40})\*{0,2}\s*:\s*(.*)$")
ISSUE_ID_RE = re.compile(r"\b(?:ISSUE[\s_-]*)(\d{1,3})\b", re.I)
CANONICAL_ID_RE = re.compile(r"\bISSUE-(\d{3})\b")
PROVISIONAL_ID_RE = re.compile(r"\b(R[123]-\d{2,3})\b")
MENTION_RE = re.compile(r"@(Author|Reviewer\s?[123]|Editor|ALL|All|all)\b")
MESSAGE_REF_RE = re.compile(r"\b[Mm](?:essage)?[\s#-]?(\d{1,6})\b")


@dataclass
class Block:
    kind: str
    fields: dict[str, str]
    raw: str
    order: int = 0

    def get(self, *names: str, default: str = "") -> str:
        for name in names:
            key = name.upper().replace(" ", "_")
            if key in self.fields and self.fields[key].strip():
                return self.fields[key].strip()
        return default


def normalize_key(key: str) -> str:
    return re.sub(r"[ /-]+", "_", key.strip().upper()).strip("_")


def normalize_issue_id(raw: str | None) -> str | None:
    """`ISSUE 7`, `issue-007`, `ISSUE-7` -> `ISSUE-007`. Provisional ids pass through."""
    if not raw:
        return None
    text = str(raw).strip()
    provisional = PROVISIONAL_ID_RE.search(text)
    if provisional:
        return provisional.group(1).upper()
    match = ISSUE_ID_RE.search(text)
    if match:
        return f"ISSUE-{int(match.group(1)):03d}"
    bare = re.fullmatch(r"\s*(\d{1,3})\s*", text)
    if bare:
        return f"ISSUE-{int(bare.group(1)):03d}"
    return None


def extract_issue_ids(text: str) -> list[str]:
    found: list[str] = []
    for match in ISSUE_ID_RE.finditer(text or ""):
        issue_id = f"ISSUE-{int(match.group(1)):03d}"
        if issue_id not in found:
            found.append(issue_id)
    for match in PROVISIONAL_ID_RE.finditer(text or ""):
        if match.group(1).upper() not in found:
            found.append(match.group(1).upper())
    return found


def extract_mentions(text: str) -> list[str]:
    mentions: list[str] = []
    for match in MENTION_RE.finditer(text or ""):
        name = match.group(1).replace(" ", "")
        name = "ALL" if name.upper() == "ALL" else name
        if name in ALL_AGENTS or name == "ALL":
            if name not in mentions:
                mentions.append(name)
    return mentions


def parse_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    for line in body.splitlines():
        if END_RE.match(line.strip()) or line.strip().startswith("<<<"):
            continue
        match = KEY_RE.match(line)
        if match:
            current = normalize_key(match.group(1))
            fields[current] = match.group(2).strip()
        elif current:
            fields[current] = (fields[current] + "\n" + line.strip()).strip()
    return {key: value.strip() for key, value in fields.items()}


def parse_blocks(text: str, kind: str | None = None) -> list[Block]:
    blocks: list[Block] = []
    order = 0
    for match in BLOCK_RE.finditer(text or ""):
        name = normalize_key(match.group(1))
        if name.startswith("END"):
            continue
        body = match.group(2)
        order += 1
        blocks.append(Block(kind=name, fields=parse_fields(body), raw=match.group(0), order=order))
    if kind:
        wanted = normalize_key(kind)
        blocks = [b for b in blocks if b.kind == wanted]
    return blocks


def strip_blocks(text: str) -> str:
    """Prose only -- used for word-limit accounting.

    A stray `<<<END X>>>` marker is itself matched by BLOCK_RE, with the prose
    that follows it as its body; that prose must be kept.
    """
    def replace(match: re.Match[str]) -> str:
        return match.group(2) if normalize_key(match.group(1)).startswith("END") else ""

    return BLOCK_RE.sub(replace, text or "").strip()


# --------------------------------------------------------------------- issues
@dataclass
class ParsedIssue:
    title: str
    category: str | None
    severity: str | None
    claim: str
    why_it_matters: str
    location: str
    proposed_remedy: str
    what_would_resolve: str
    confidence: str | None
    raw: str

    @property
    def description(self) -> str:
        parts = [f"CLAIM: {self.claim}"]
        if self.why_it_matters:
            parts.append(f"WHY IT MATTERS: {self.why_it_matters}")
        if self.proposed_remedy:
            parts.append(f"PROPOSED REMEDY: {self.proposed_remedy}")
        if self.what_would_resolve:
            parts.append(f"WHAT WOULD RESOLVE THIS: {self.what_would_resolve}")
        return "\n".join(parts)


def parse_issues(text: str) -> list[ParsedIssue]:
    issues: list[ParsedIssue] = []
    for block in parse_blocks(text, "ISSUE"):
        title = block.get("ISSUE_TITLE", "TITLE")
        claim = block.get("CLAIM", "PROBLEM", "DESCRIPTION")
        if not title and not claim:
            continue
        issues.append(ParsedIssue(
            title=title or claim[:80],
            category=normalize_enum(block.get("CATEGORY"), CATEGORIES, "other"),
            severity=normalize_enum(block.get("SEVERITY"), SEVERITIES, "moderate"),
            claim=claim,
            why_it_matters=block.get("WHY_IT_MATTERS", "WHY_THIS_MATTERS", "WHY"),
            location=block.get("MANUSCRIPT_LOCATION", "LOCATION"),
            proposed_remedy=block.get("PROPOSED_REMEDY", "REMEDY"),
            what_would_resolve=block.get("WHAT_WOULD_RESOLVE_THIS_CONCERN",
                                         "WHAT_WOULD_RESOLVE_THIS", "WHAT_WOULD_RESOLVE",
                                         "RESOLUTION_CRITERION"),
            confidence=normalize_enum(block.get("CONFIDENCE"), CONFIDENCES, "medium"),
            raw=block.raw.strip(),
        ))
    return issues


# ----------------------------------------------------------- position changes
@dataclass
class ParsedPositionChange:
    issue_id: str | None
    previous: str | None
    new: str | None
    reason: str
    triggered_by_message_id: int | None
    triggered_by_agent: str | None
    remaining_concern: str
    raw: str


def parse_position_changes(text: str) -> list[ParsedPositionChange]:
    changes: list[ParsedPositionChange] = []
    for block in parse_blocks(text):
        if block.kind not in {"POSITION_CHANGE", "POSITION_UPDATE"}:
            continue
        trigger = block.get("TRIGGERED_BY", "TRIGGER")
        message_match = MESSAGE_REF_RE.search(trigger)
        mentions = extract_mentions(trigger)
        agent = mentions[0] if mentions else None
        if agent is None:
            for name in ALL_AGENTS:
                if re.search(rf"\b{name}\b", trigger, re.I):
                    agent = name
                    break
        changes.append(ParsedPositionChange(
            issue_id=normalize_issue_id(block.get("ISSUE", "ISSUE_ID")),
            previous=block.get("PREVIOUS_POSITION", "PREVIOUS", "FROM") or None,
            new=block.get("NEW_POSITION", "NEW", "TO") or None,
            reason=block.get("REASON", "RATIONALE"),
            triggered_by_message_id=int(message_match.group(1)) if message_match else None,
            triggered_by_agent=agent,
            remaining_concern=block.get("REMAINING_CONCERN", "REMAINING_CONCERN_IF_ANY",
                                        "REMAINING"),
            raw=block.raw.strip(),
        ))
    return changes


# --------------------------------------------------------------- editor moves
@dataclass
class LedgerUpdate:
    issue_id: str | None
    status: str | None
    severity: str | None
    priority: str | None
    summary: str
    required_action: str
    decision: str
    reason: str
    raw: str


@dataclass
class EditorMoves:
    agenda: list[dict[str, Any]] = field(default_factory=list)
    updates: list[LedgerUpdate] = field(default_factory=list)
    new_issues: list[dict[str, Any]] = field(default_factory=list)
    merges: list[dict[str, str]] = field(default_factory=list)
    splits: list[dict[str, Any]] = field(default_factory=list)
    reopens: list[dict[str, str]] = field(default_factory=list)
    terminate: str | None = None


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;]| and ", value or "") if item.strip()]


def parse_editor_moves(text: str) -> EditorMoves:
    moves = EditorMoves()
    for block in parse_blocks(text):
        kind = block.kind
        if kind in {"AGENDA", "AGENDA_ITEM"}:
            issue_ids = [i for i in (normalize_issue_id(x) for x in
                                     _split_list(block.get("ISSUES", "ISSUE", "ISSUE_IDS"))) if i]
            speakers = [s.lstrip("@").replace(" ", "") for s in
                        _split_list(block.get("SPEAKERS", "CALL_ON", "RESPOND"))]
            speakers = [s for s in speakers if s in ALL_AGENTS]
            moves.agenda.append({
                "issue_ids": issue_ids,
                "speakers": speakers,
                "question": block.get("QUESTION", "PROMPT", "INSTRUCTION"),
            })
        elif kind in {"LEDGER_UPDATE", "ISSUE_UPDATE", "STATUS_UPDATE"}:
            moves.updates.append(LedgerUpdate(
                issue_id=normalize_issue_id(block.get("ISSUE", "ISSUE_ID")),
                status=normalize_status(block.get("STATUS")),
                severity=normalize_enum(block.get("SEVERITY"), SEVERITIES),
                priority=normalize_enum(block.get("PRIORITY"), ("essential", "important", "minor")),
                summary=block.get("SUMMARY", "CURRENT_SUMMARY", "EDITOR_SYNTHESIS"),
                required_action=block.get("REQUIRED_ACTION", "ACTION"),
                decision=block.get("DECISION", "EDITOR_DECISION"),
                reason=block.get("REASON", "RATIONALE"),
                raw=block.raw.strip(),
            ))
        elif kind in {"NEW_ISSUE", "CREATE_ISSUE"}:
            moves.new_issues.append({
                "title": block.get("TITLE", "ISSUE_TITLE"),
                "category": normalize_enum(block.get("CATEGORY"), CATEGORIES, "other"),
                "severity": normalize_enum(block.get("SEVERITY"), SEVERITIES, "moderate"),
                "description": block.get("DESCRIPTION", "CLAIM", "SUMMARY"),
                "raised_by": [s.lstrip("@").replace(" ", "") for s in
                              _split_list(block.get("RAISED_BY", "SOURCE"))] or ["Editor"],
                "location": block.get("MANUSCRIPT_LOCATION", "LOCATION"),
                "original_comment": block.get("ORIGINAL_COMMENT", "SOURCE_TEXT"),
                "question": block.get("QUESTION_FOR_AUTHOR", "QUESTION"),
            })
        elif kind == "MERGE":
            raw_target = block.get("INTO", "TARGET", "CANONICAL")
            # The Editor may not know canonical ids yet and may name the target
            # by title; keep the raw string for title resolution downstream.
            target = normalize_issue_id(raw_target) or raw_target
            sources = [normalize_issue_id(x) or x for x in
                       _split_list(block.get("SOURCES", "FROM", "ISSUES", "MERGE"))]
            for source in sources:
                if source and target and source != target:
                    moves.merges.append({"source": source, "target": target,
                                         "reason": block.get("REASON")})
        elif kind == "SPLIT":
            moves.splits.append({
                "issue_id": (normalize_issue_id(block.get("ISSUE", "ISSUE_ID", "FROM"))
                             or block.get("ISSUE", "ISSUE_ID", "FROM") or None),
                "titles": [t for t in _split_list(block.get("INTO", "TITLES", "PARTS")) if t],
                "descriptions": block.get("DESCRIPTIONS", "DESCRIPTION"),
                "reason": block.get("REASON"),
            })
        elif kind == "REOPEN":
            issue_id = normalize_issue_id(block.get("ISSUE", "ISSUE_ID"))
            if issue_id:
                moves.reopens.append({
                    "issue_id": issue_id,
                    "reason": block.get("REASON", "NEW_EVIDENCE", "JUSTIFICATION"),
                    "status": normalize_status(block.get("STATUS")) or "OPEN",
                })
        elif kind in {"TERMINATE", "END_DELIBERATION", "CLOSE_DELIBERATION"}:
            moves.terminate = (block.get("REASON", "RATIONALE")
                               or strip_blocks(block.raw) or "editor ended deliberation")
    return moves


# ------------------------------------------------------------ author revisions
@dataclass
class ParsedRevision:
    issue_id: str | None
    kind: str          # substantive | analysis | clarification | exposition | no change
    location: str
    change: str
    analysis: str
    claims_change: str
    raw: str


def parse_revisions(text: str) -> list[ParsedRevision]:
    revisions: list[ParsedRevision] = []
    for block in parse_blocks(text):
        if block.kind not in {"REVISION", "PROPOSED_REVISION", "CHANGE"}:
            continue
        revisions.append(ParsedRevision(
            issue_id=normalize_issue_id(block.get("ISSUE", "ISSUE_ID")),
            kind=(block.get("TYPE", "KIND", "CLASSIFICATION") or "clarification").lower(),
            location=block.get("MANUSCRIPT_LOCATION", "LOCATION"),
            change=block.get("TEXTUAL_CHANGE", "CHANGE", "INTENDED_CHANGE", "TEXT"),
            analysis=block.get("ANALYSIS_REQUIRED", "ANALYSIS"),
            claims_change=block.get("SUBSTANTIVE_CLAIMS_CHANGE", "CLAIMS_CHANGE", "CLAIM_CHANGE"),
            raw=block.raw.strip(),
        ))
    return revisions


def resolve_recipients(text: str, fallback: Iterable[str]) -> list[str]:
    mentions = extract_mentions(text)
    if not mentions:
        return list(fallback)
    if "ALL" in mentions:
        return ["ALL"]
    return mentions
