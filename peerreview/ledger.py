"""Ledger operations: applying editor moves and recording position changes.

Two invariants are enforced here rather than trusted to the models:

* `issue_positions.triggered_by_message_id` always points at a real message in
  the same run (or is NULL) -- influence paths must be reconstructible.
* An issue in a terminal status can only move back to an open status through
  an explicit reopen with a reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .db import ReopenWithoutReason, Store
from .models import (STATUS_OPEN, is_terminal, normalize_enum, normalize_status, SEVERITIES)
from .parsing import EditorMoves, ParsedPositionChange, parse_position_changes
from .util import jload


@dataclass
class LedgerEvent:
    kind: str
    issue_id: str | None
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind} {self.issue_id or ''}: {self.detail}".strip()


def normalize_position(text: str | None) -> str | None:
    if not text:
        return None
    status = normalize_status(text)
    if status:
        return status
    return " ".join(str(text).split()).strip().upper()[:120]


def next_canonical_id(store: Store, run_id: str) -> str:
    return f"ISSUE-{store.next_issue_number(run_id):03d}"


def resolve_trigger(store: Store, run_id: str, change: ParsedPositionChange,
                    agent_id: str, fallback_message_id: int | None = None) -> tuple[int | None, str | None]:
    """Resolve TRIGGERED BY to a real message id, or drop the link.

    Order: the cited id if it exists; else the most recent message from the
    cited agent about that issue; else the message under discussion.
    """
    cited = change.triggered_by_message_id
    if cited is not None and store.get_message(run_id, cited) is not None:
        row = store.get_message(run_id, cited)
        return int(cited), change.triggered_by_agent or row["sender"]
    if change.triggered_by_agent:
        rows = store.query(
            "SELECT * FROM messages WHERE run_id=? AND sender=? ORDER BY id DESC LIMIT 40",
            (run_id, change.triggered_by_agent))
        for row in rows:
            issue_ids = jload(row["issue_ids"], []) or []
            if change.issue_id and change.issue_id in issue_ids:
                return int(row["id"]), change.triggered_by_agent
        if rows:
            return int(rows[0]["id"]), change.triggered_by_agent
    if fallback_message_id is not None and store.get_message(run_id, fallback_message_id):
        row = store.get_message(run_id, fallback_message_id)
        return int(fallback_message_id), row["sender"]
    return None, change.triggered_by_agent


def record_position_changes(store: Store, run_id: str, *, round: int, agent_id: str,
                            text: str, message_id: int,
                            known_issue_ids: Sequence[str] | None = None) -> list[LedgerEvent]:
    """Persist POSITION CHANGE blocks emitted by `agent_id`."""
    events: list[LedgerEvent] = []
    for change in parse_position_changes(text):
        issue_id = change.issue_id
        if issue_id is None and known_issue_ids and len(known_issue_ids) == 1:
            issue_id = known_issue_ids[0]
        if issue_id is None or store.get_issue(run_id, issue_id) is None:
            continue
        stored_previous = store.latest_position(run_id, issue_id, agent_id)
        claimed_previous = normalize_position(change.previous)
        new_position = normalize_position(change.new)
        if not new_position:
            continue
        changed_from = stored_previous or claimed_previous
        rationale = change.reason
        if (stored_previous and claimed_previous
                and stored_previous.upper() != claimed_previous.upper()):
            rationale = (f"{rationale}\n[recorded previous position was '{stored_previous}'; "
                         f"agent stated '{claimed_previous}']").strip()
        trigger_id, trigger_agent = resolve_trigger(store, run_id, change, agent_id,
                                                    fallback_message_id=None)
        store.add_position(run_id, issue_id=issue_id, round=round, agent=agent_id,
                           position=new_position, changed_from=changed_from, rationale=rationale,
                           triggered_by_message_id=trigger_id, triggered_by_agent=trigger_agent,
                           remaining_concern=change.remaining_concern)
        store.add_issue_event(run_id, issue_id, round, agent_id, "position_change",
                              reason=change.reason,
                              payload={"from": changed_from, "to": new_position,
                                       "triggered_by": trigger_id},
                              message_id=message_id)
        events.append(LedgerEvent("position_change", issue_id,
                                  f"{agent_id}: {changed_from or 'n/a'} -> {new_position}",
                                  {"agent": agent_id, "from": changed_from, "to": new_position,
                                   "triggered_by_message_id": trigger_id,
                                   "triggered_by_agent": trigger_agent}))
    return events


def seed_positions(store: Store, run_id: str, issue_id: str, agents: Iterable[str], *,
                   round: int, position: str) -> None:
    for agent in agents:
        if store.latest_position(run_id, issue_id, agent) is None:
            store.add_position(run_id, issue_id=issue_id, round=round, agent=agent,
                               position=position, changed_from=None,
                               rationale="initial position from independent review")


def apply_editor_moves(store: Store, run_id: str, *, round: int, moves: EditorMoves,
                       message_id: int, actor: str = "Editor",
                       provisional_lookup: dict[str, str] | None = None) -> list[LedgerEvent]:
    """Apply NEW ISSUE / MERGE / SPLIT / LEDGER UPDATE / REOPEN blocks."""
    events: list[LedgerEvent] = []
    title_to_id: dict[str, str] = {}
    for row in store.list_issues(run_id, provisional=None, include_merged=True):
        title_to_id.setdefault(_norm_title(row["title"]), row["issue_id"])

    # 1. New canonical issues
    for spec in moves.new_issues:
        title = (spec.get("title") or "").strip()
        if not title:
            continue
        issue_id = next_canonical_id(store, run_id)
        raised = [_norm_agent(a) for a in (spec.get("raised_by") or []) if _norm_agent(a)]
        store.create_issue(
            run_id, issue_id=issue_id, title=title, actor=actor, round=round,
            description=spec.get("description", ""), category=spec.get("category"),
            severity=spec.get("severity"), status=STATUS_OPEN, raised_by=raised,
            manuscript_location=spec.get("location", ""),
            original_comment=spec.get("original_comment", ""),
            current_summary=spec.get("description", ""), message_id=message_id)
        if spec.get("question"):
            store.update_issue(run_id, issue_id, actor=actor, round=round,
                               required_action=f"Answer: {spec['question']}", message_id=message_id)
        title_to_id[_norm_title(title)] = issue_id
        events.append(LedgerEvent("issue_created", issue_id, title,
                                  {"raised_by": raised, "severity": spec.get("severity")}))

    # 2. Merges (provisional -> canonical, or canonical -> canonical)
    for merge in moves.merges:
        source = _resolve_id(store, run_id, merge.get("source"), title_to_id, provisional_lookup)
        target = _resolve_id(store, run_id, merge.get("target"), title_to_id, provisional_lookup)
        if not source or not target or source == target:
            continue
        try:
            store.merge_issue(run_id, source_id=source, target_id=target, actor=actor,
                              round=round, reason=merge.get("reason") or "", message_id=message_id)
            events.append(LedgerEvent("merge", target, f"absorbed {source}",
                                      {"source": source, "target": target}))
        except KeyError:
            continue

    # 3. Splits
    for split in moves.splits:
        source = _resolve_id(store, run_id, split.get("issue_id"), title_to_id, provisional_lookup)
        if not source:
            continue
        parent = store.get_issue(run_id, source)
        if parent is None:
            continue
        created: list[str] = []
        for title in split.get("titles", []):
            issue_id = next_canonical_id(store, run_id)
            store.create_issue(
                run_id, issue_id=issue_id, title=title, actor=actor, round=round,
                description=split.get("descriptions", "") or parent["description"],
                category=parent["category"], severity=parent["severity"], status=STATUS_OPEN,
                raised_by=jload(parent["raised_by"], []) or [],
                manuscript_location=parent["manuscript_location"],
                original_comment=parent["original_comment"],
                current_summary=f"Split from {source}: {title}", message_id=message_id)
            created.append(issue_id)
            title_to_id[_norm_title(title)] = issue_id
        if created:
            store.add_issue_event(run_id, source, round, actor, "split",
                                  reason=split.get("reason"), payload={"into": created},
                                  message_id=message_id)
            store.update_issue(run_id, source, actor=actor, round=round,
                               reason=split.get("reason"),
                               current_summary=((parent["current_summary"] or "") +
                                                f"\n[split into {', '.join(created)}]").strip(),
                               message_id=message_id)
            events.append(LedgerEvent("split", source, f"into {', '.join(created)}",
                                      {"into": created}))

    # 4. Reopens (explicit reason required)
    for reopen in moves.reopens:
        issue_id = _resolve_id(store, run_id, reopen["issue_id"], title_to_id, provisional_lookup)
        reason = (reopen.get("reason") or "").strip()
        if not issue_id or not reason:
            events.append(LedgerEvent("reopen_rejected", reopen.get("issue_id"),
                                      "reopen requires a reason"))
            continue
        try:
            store.update_issue(run_id, issue_id, actor=actor, round=round, reason=reason,
                               allow_reopen=True, status=reopen.get("status") or STATUS_OPEN,
                               closed_round=None, message_id=message_id)
            events.append(LedgerEvent("reopen", issue_id, reason))
        except ReopenWithoutReason as exc:
            events.append(LedgerEvent("reopen_rejected", issue_id, str(exc)))

    # 5. Status / field updates
    for update in moves.updates:
        issue_id = _resolve_id(store, run_id, update.issue_id, title_to_id, provisional_lookup)
        if not issue_id:
            continue
        issue = store.get_issue(run_id, issue_id)
        if issue is None:
            continue
        fields: dict[str, Any] = {}
        if update.status and update.status != issue["status"]:
            fields["status"] = update.status
            if is_terminal(update.status):
                fields["closed_round"] = round
        if update.severity:
            fields["severity"] = normalize_enum(update.severity, SEVERITIES, issue["severity"])
        if update.priority:
            fields["priority"] = update.priority
        if update.summary:
            fields["current_summary"] = update.summary
        if update.required_action:
            fields["required_action"] = update.required_action
        if update.decision:
            fields["editor_decision"] = update.decision
        if not fields:
            continue
        try:
            store.update_issue(run_id, issue_id, actor=actor, round=round,
                               reason=update.reason or update.decision, message_id=message_id,
                               allow_reopen=bool(update.reason), **fields)
            if "status" in fields:
                events.append(LedgerEvent("status", issue_id,
                                          f"{issue['status']} -> {fields['status']}",
                                          {"from": issue["status"], "to": fields["status"]}))
            else:
                events.append(LedgerEvent("update", issue_id, ", ".join(sorted(fields))))
        except ReopenWithoutReason as exc:
            events.append(LedgerEvent("reopen_rejected", issue_id, str(exc)))
    return events


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (title or "").lower()).strip()


def _norm_agent(name: str) -> str:
    text = (name or "").strip().lstrip("@").replace(" ", "")
    match = re.fullmatch(r"R(\d)", text, re.I)
    if match:
        return f"Reviewer{match.group(1)}"
    if text.lower().startswith("reviewer"):
        return "Reviewer" + re.sub(r"\D", "", text)
    if text.lower().startswith("author"):
        return "Author"
    if text.lower().startswith("editor"):
        return "Editor"
    return text


def _resolve_id(store: Store, run_id: str, raw: str | None, title_to_id: dict[str, str],
                provisional_lookup: dict[str, str] | None) -> str | None:
    if not raw:
        return None
    candidate = raw.strip()
    if store.get_issue(run_id, candidate) is not None:
        return candidate
    if provisional_lookup and candidate.upper() in provisional_lookup:
        return provisional_lookup[candidate.upper()]
    normalized = _norm_title(candidate)
    if normalized in title_to_id:
        return title_to_id[normalized]
    for title, issue_id in title_to_id.items():
        if normalized and (normalized in title or title in normalized):
            return issue_id
    return None


def all_terminal(store: Store, run_id: str) -> bool:
    issues = store.list_issues(run_id)
    return bool(issues) and all(is_terminal(row["status"]) for row in issues)
