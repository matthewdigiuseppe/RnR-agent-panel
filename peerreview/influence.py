"""Influence reconstruction: whose argument changed whose mind.

Every position change carries a link to the message that triggered it, so the
deliberation can be replayed as a directed graph of persuasion events.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import Store
from .models import is_terminal
from .util import jload


@dataclass
class InfluenceEdge:
    issue_id: str
    round: int
    source: str | None          # who persuaded
    target: str                 # who moved
    message_id: int | None
    from_position: str | None
    to_position: str
    rationale: str
    remaining_concern: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id, "round": self.round, "source": self.source,
            "target": self.target, "triggered_by_message_id": self.message_id,
            "from": self.from_position, "to": self.to_position,
            "rationale": self.rationale, "remaining_concern": self.remaining_concern,
        }


@dataclass
class IssueInfluence:
    issue_id: str
    title: str
    status: str
    edges: list[InfluenceEdge] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    editor_decision: str = ""

    @property
    def path_string(self) -> str:
        return " -> ".join(self.path) if self.path else "(no position changes)"

    def to_dict(self) -> dict[str, Any]:
        return {"issue_id": self.issue_id, "title": self.title, "status": self.status,
                "influence_path": self.path_string, "edges": [e.to_dict() for e in self.edges],
                "editor_decision": self.editor_decision}


def collect_edges(store: Store, run_id: str, issue_id: str | None = None) -> list[InfluenceEdge]:
    edges: list[InfluenceEdge] = []
    for row in store.positions(run_id, issue_id):
        if not row["changed_from"] or row["changed_from"] == row["position"]:
            continue  # initial position, not a change
        source = row["triggered_by_agent"]
        if source is None and row["triggered_by_message_id"] is not None:
            message = store.get_message(run_id, row["triggered_by_message_id"])
            source = message["sender"] if message else None
        edges.append(InfluenceEdge(
            issue_id=row["issue_id"], round=row["round"], source=source, target=row["agent"],
            message_id=row["triggered_by_message_id"], from_position=row["changed_from"],
            to_position=row["position"], rationale=row["rationale"] or "",
            remaining_concern=row["remaining_concern"] or ""))
    return edges


def invalid_edges(store: Store, run_id: str) -> list[dict[str, Any]]:
    """Position changes whose trigger does not resolve to a message in this run."""
    broken = []
    for row in store.positions(run_id):
        message_id = row["triggered_by_message_id"]
        if message_id is None:
            continue
        if store.get_message(run_id, message_id) is None:
            broken.append({"position_id": row["id"], "issue_id": row["issue_id"],
                           "agent": row["agent"], "triggered_by_message_id": message_id})
    return broken


def _author_committed(store: Store, run_id: str, issue_id: str) -> bool:
    """True when the Author proposed a concrete revision for this issue."""
    from .parsing import parse_revisions

    for row in store.all_messages(run_id):
        if row["sender"] != "Author":
            continue
        if any(revision.issue_id == issue_id for revision in parse_revisions(row["content"])):
            return True
    return False


def issue_influence(store: Store, run_id: str, issue_id: str) -> IssueInfluence:
    issue = store.get_issue(run_id, issue_id)
    if issue is None:
        raise KeyError(issue_id)
    edges = collect_edges(store, run_id, issue_id)
    path: list[str] = []
    for edge in edges:
        if edge.source and (not path or path[-1] != edge.source):
            path.append(edge.source)
        if not path or path[-1] != edge.target:
            path.append(edge.target)
    if _author_committed(store, run_id, issue_id) and (not path or path[-1] != "Author"):
        path.append("Author")
    if is_terminal(issue["status"]) and (edges or issue["editor_decision"]):
        path.append("Editor decision")
    return IssueInfluence(issue_id=issue_id, title=issue["title"], status=issue["status"],
                          edges=edges, path=path,
                          editor_decision=issue["editor_decision"] or "")


def influence_report(store: Store, run_id: str) -> dict[str, Any]:
    issues = store.list_issues(run_id)
    per_issue = [issue_influence(store, run_id, row["issue_id"]) for row in issues]
    persuaded: dict[str, int] = {}
    updated: dict[str, int] = {}
    pairs: dict[str, int] = {}
    for influence in per_issue:
        for edge in influence.edges:
            updated[edge.target] = updated.get(edge.target, 0) + 1
            if edge.source:
                persuaded[edge.source] = persuaded.get(edge.source, 0) + 1
                key = f"{edge.source} -> {edge.target}"
                pairs[key] = pairs.get(key, 0) + 1
    return {
        "issues": [influence.to_dict() for influence in per_issue],
        "persuasion_counts": dict(sorted(persuaded.items(), key=lambda kv: -kv[1])),
        "update_counts": dict(sorted(updated.items(), key=lambda kv: -kv[1])),
        "edge_counts": dict(sorted(pairs.items(), key=lambda kv: -kv[1])),
        "total_position_changes": sum(len(i.edges) for i in per_issue),
        "invalid_edges": invalid_edges(store, run_id),
    }


def narrate_issue(store: Store, run_id: str, influence: IssueInfluence) -> str:
    """Prose reconstruction of how an issue moved, in the style of a case note."""
    issue = store.get_issue(run_id, influence.issue_id)
    lines = [f"**{influence.issue_id}: {influence.title}**",
             f"Final status: {influence.status}"]
    raised = ", ".join(jload(issue["raised_by"], []) or []) if issue else ""
    if raised:
        lines.append(f"Raised by: {raised}")
    if not influence.edges:
        lines.append("No participant changed position on this issue.")
    for edge in influence.edges:
        trigger = f" after M{edge.message_id}" if edge.message_id else ""
        source = f" ({edge.source}'s argument{trigger})" if edge.source else trigger
        lines.append(f"- Round {edge.round}: {edge.target} moved "
                     f"{edge.from_position} -> {edge.to_position}{source}."
                     + (f" Reason: {edge.rationale.strip()}" if edge.rationale else ""))
        if edge.remaining_concern and edge.remaining_concern.lower() not in {"none", "n/a", ""}:
            lines.append(f"  Remaining concern: {edge.remaining_concern}")
    if influence.editor_decision:
        lines.append(f"Editor decision: {influence.editor_decision}")
    lines.append(f"Influence path: {influence.path_string}")
    return "\n".join(lines)
