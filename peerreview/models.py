"""Controlled vocabularies and dataclasses shared across the system.

Severity and status are deliberately kept conceptually separate: severity is a
property of the problem, status is a property of the deliberation about it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ROLE_AUTHOR = "author"
ROLE_REVIEWER = "reviewer"
ROLE_EDITOR = "editor"

AUTHOR = "Author"
EDITOR = "Editor"
REVIEWERS = ("Reviewer1", "Reviewer2", "Reviewer3")
ALL_AGENTS = (AUTHOR,) + REVIEWERS + (EDITOR,)
ALL = "ALL"
HUMAN = "HUMAN"

CATEGORIES = (
    "contribution", "theory", "literature", "research design", "identification",
    "measurement", "statistical inference", "interpretation", "robustness",
    "external validity", "exposition", "scope", "presentation", "other",
)

SEVERITIES = ("major", "moderate", "minor")
CONFIDENCES = ("high", "medium", "low")
PRIORITIES = ("essential", "important", "minor")

# Issue status taxonomy. Terminal statuses end deliberation for that issue.
STATUS_OPEN = "OPEN"
STATUS_SUBSTANTIVE = "SUBSTANTIVE REVISION REQUIRED"
STATUS_NEW_ANALYSIS = "NEW ANALYSIS REQUIRED"
STATUS_ROBUSTNESS_OPTIONAL = "ROBUSTNESS CHECK OPTIONAL"
STATUS_CLARIFICATION = "CLARIFICATION REQUIRED"
STATUS_EXPOSITION = "EXPOSITION ONLY"
STATUS_REBUTTAL_ACCEPTED = "AUTHOR REBUTTAL ACCEPTED / NO CHANGE REQUIRED"
STATUS_WITHDRAWN = "REVIEWER WITHDREW OBJECTION"
STATUS_OUT_OF_SCOPE = "OUT OF SCOPE"
STATUS_UNRESOLVED = "UNRESOLVED DISAGREEMENT"
STATUS_RESOLVED = "RESOLVED"
STATUS_CLOSED = "CLOSED"

STATUSES = (
    STATUS_OPEN, STATUS_SUBSTANTIVE, STATUS_NEW_ANALYSIS, STATUS_ROBUSTNESS_OPTIONAL,
    STATUS_CLARIFICATION, STATUS_EXPOSITION, STATUS_REBUTTAL_ACCEPTED, STATUS_WITHDRAWN,
    STATUS_OUT_OF_SCOPE, STATUS_UNRESOLVED, STATUS_RESOLVED, STATUS_CLOSED,
)

# Statuses that require no further deliberation. UNRESOLVED DISAGREEMENT is a
# legitimate terminal state: the system never forces consensus.
TERMINAL_STATUSES = frozenset({
    STATUS_SUBSTANTIVE, STATUS_NEW_ANALYSIS, STATUS_ROBUSTNESS_OPTIONAL,
    STATUS_CLARIFICATION, STATUS_EXPOSITION, STATUS_REBUTTAL_ACCEPTED,
    STATUS_WITHDRAWN, STATUS_OUT_OF_SCOPE, STATUS_UNRESOLVED, STATUS_RESOLVED,
    STATUS_CLOSED,
})

# Statuses meaning "nothing needs to change in the manuscript".
DISMISSED_STATUSES = frozenset({
    STATUS_REBUTTAL_ACCEPTED, STATUS_WITHDRAWN, STATUS_OUT_OF_SCOPE,
})

STATUS_TO_CLASSIFICATION = {
    STATUS_SUBSTANTIVE: "substantive revision",
    STATUS_NEW_ANALYSIS: "new analysis",
    STATUS_CLARIFICATION: "clarification",
    STATUS_EXPOSITION: "exposition",
    STATUS_ROBUSTNESS_OPTIONAL: "optional",
    STATUS_REBUTTAL_ACCEPTED: "no change",
    STATUS_WITHDRAWN: "no change",
    STATUS_OUT_OF_SCOPE: "no change",
    STATUS_UNRESOLVED: "unresolved disagreement",
    STATUS_RESOLVED: "clarification",
    STATUS_CLOSED: "no change",
    STATUS_OPEN: "unresolved disagreement",
}

MESSAGE_TYPES = (
    "independent_review", "review_issue", "consolidation", "ledger", "agenda",
    "deliberation", "author_response", "position_change", "editor_direction",
    "human_intervention", "system", "revision",
)


def normalize_status(value: str | None) -> str | None:
    """Map free-text model output onto the controlled status vocabulary."""
    if not value:
        return None
    raw = " ".join(str(value).split()).strip().strip(".").upper()
    if raw in STATUSES:
        return raw
    aliases = {
        "NO CHANGE REQUIRED": STATUS_REBUTTAL_ACCEPTED,
        "AUTHOR REBUTTAL ACCEPTED": STATUS_REBUTTAL_ACCEPTED,
        "REBUTTAL ACCEPTED": STATUS_REBUTTAL_ACCEPTED,
        "WITHDRAWN": STATUS_WITHDRAWN,
        "OBJECTION WITHDRAWN": STATUS_WITHDRAWN,
        "REVIEWER WITHDRAWS OBJECTION": STATUS_WITHDRAWN,
        "SUBSTANTIVE REVISION": STATUS_SUBSTANTIVE,
        "NEW ANALYSIS": STATUS_NEW_ANALYSIS,
        "ANALYSIS REQUIRED": STATUS_NEW_ANALYSIS,
        "CLARIFICATION": STATUS_CLARIFICATION,
        "CLARIFICATION ONLY": STATUS_CLARIFICATION,
        "EXPOSITION": STATUS_EXPOSITION,
        "ROBUSTNESS OPTIONAL": STATUS_ROBUSTNESS_OPTIONAL,
        "OPTIONAL ROBUSTNESS CHECK": STATUS_ROBUSTNESS_OPTIONAL,
        "UNRESOLVED": STATUS_UNRESOLVED,
        "DISAGREEMENT": STATUS_UNRESOLVED,
        "OUT OF SCOPE": STATUS_OUT_OF_SCOPE,
    }
    if raw in aliases:
        return aliases[raw]
    for status in STATUSES:
        if raw.startswith(status) or status.startswith(raw):
            return status
    return None


def normalize_enum(value: str | None, allowed: tuple[str, ...], default: str | None = None) -> str | None:
    if not value:
        return default
    raw = " ".join(str(value).split()).strip().strip(".").lower()
    if raw in allowed:
        return raw
    for item in allowed:
        if raw.startswith(item) or item in raw:
            return item
    return default


def is_terminal(status: str | None) -> bool:
    return bool(status) and status in TERMINAL_STATUSES


@dataclass
class AgentSpec:
    """Everything needed to reconstruct an agent, including in a subprocess."""
    id: str
    role: str
    name: str
    expertise: str
    system_prompt: str
    provider: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "role": self.role, "name": self.name,
            "expertise": self.expertise, "system_prompt": self.system_prompt,
            "provider": self.provider, "model": self.model, "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSpec":
        return cls(
            id=data["id"], role=data["role"], name=data["name"],
            expertise=data.get("expertise", ""), system_prompt=data["system_prompt"],
            provider=data["provider"], model=data["model"], params=data.get("params", {}) or {},
        )
