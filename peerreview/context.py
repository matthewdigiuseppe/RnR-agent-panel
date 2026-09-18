"""Per-agent context assembly under a token budget.

The active context prefers, in order:
  1. manuscript sections relevant to the issues on the table;
  2. the history of those issues;
  3. one-line summaries of settled issues (not their transcripts);
  4. the most recent shared-room messages.

The full archival transcript always lives in SQLite; it is never re-sent
wholesale.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Sequence

from .backends.base import ChatMessage
from .manuscript import ManuscriptBundle
from .models import is_terminal
from .util import estimate_tokens, jload, truncate_tokens


@dataclass
class Part:
    name: str
    text: str
    priority: int      # 1 = keep at all costs
    floor_tokens: int = 120

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def render_message(row: sqlite3.Row, *, max_words: int | None = None) -> str:
    recipients = jload(row["recipients"], []) or []
    issue_ids = jload(row["issue_ids"], []) or []
    head = f"[M{row['id']}] {row['sender']} -> {', '.join(recipients)} (round {row['round']}"
    if issue_ids:
        head += f", {', '.join(issue_ids)}"
    head += ")"
    if row["is_human"]:
        head += " [HUMAN INTERVENTION]"
    body = row["content"].strip()
    if max_words:
        words = body.split()
        if len(words) > max_words:
            body = " ".join(words[:max_words]) + " [...]"
    return f"{head}\n{body}"


def render_issue_line(row: sqlite3.Row) -> str:
    raised = ", ".join(jload(row["raised_by"], []) or [])
    line = f"- {row['issue_id']} [{row['status']}] ({row['severity'] or 'n/a'}, {row['category'] or 'n/a'}) {row['title']}"
    if raised:
        line += f" -- raised by {raised}"
    summary = (row["current_summary"] or "").strip()
    if summary:
        line += f"\n    summary: {summary}"
    action = (row["required_action"] or "").strip()
    if action:
        line += f"\n    required action: {action}"
    return line


def render_issue_full(row: sqlite3.Row) -> str:
    parts = [
        f"{row['issue_id']}: {row['title']}",
        f"  status: {row['status']}   severity: {row['severity'] or 'n/a'}   "
        f"category: {row['category'] or 'n/a'}",
        f"  raised by: {', '.join(jload(row['raised_by'], []) or []) or 'n/a'}",
        f"  location: {row['manuscript_location'] or 'n/a'}",
    ]
    if row["description"]:
        parts.append(f"  description: {row['description']}")
    if row["current_summary"]:
        parts.append(f"  editor synthesis: {row['current_summary']}")
    if row["required_action"]:
        parts.append(f"  required action: {row['required_action']}")
    if row["editor_decision"]:
        parts.append(f"  editor decision: {row['editor_decision']}")
    return "\n".join(parts)


class ContextBuilder:
    def __init__(self, store, run_id: str, bundle: ManuscriptBundle, *,
                 max_prompt_tokens: int = 60000, recent_messages: int = 14,
                 own_turn_memory: int = 3, manuscript_sections_per_issue: int = 2,
                 message_excerpt_words: int = 260):
        self.store = store
        self.run_id = run_id
        self.bundle = bundle
        self.max_prompt_tokens = max_prompt_tokens
        self.recent_messages = recent_messages
        self.own_turn_memory = own_turn_memory
        self.manuscript_sections_per_issue = manuscript_sections_per_issue
        self.message_excerpt_words = message_excerpt_words

    # ------------------------------------------------------------------ parts
    def _manuscript_part(self, focus_issues: Sequence[sqlite3.Row], phase: str) -> Part:
        if phase == "independent_review":
            budget = int(self.max_prompt_tokens * 0.6)
            return Part("MANUSCRIPT", self.bundle.full_text(max_tokens=budget), priority=1,
                        floor_tokens=budget // 2)
        queries = []
        for issue in focus_issues:
            queries.append(" ".join(filter(None, [
                issue["title"], issue["manuscript_location"] or "",
                (issue["description"] or "")[:400]])))
        chosen: list = []
        seen: set[str] = set()
        for query in queries:
            for section in self.bundle.find_sections(
                    query, k=self.manuscript_sections_per_issue, budget_tokens=4000):
                if section.id not in seen:
                    seen.add(section.id)
                    chosen.append(section)
        if not chosen:
            return Part("MANUSCRIPT EXCERPTS", "(no specific sections selected this turn)",
                        priority=3, floor_tokens=20)
        body = "\n\n".join(section.render(max_tokens=1400) for section in chosen)
        return Part("MANUSCRIPT EXCERPTS (relevant to the issues on the table)", body,
                    priority=1, floor_tokens=600)

    def _ledger_part(self, focus_ids: Sequence[str]) -> Part:
        issues = self.store.list_issues(self.run_id)
        if not issues:
            return Part("ISSUE LEDGER", "(no issues yet)", priority=2, floor_tokens=20)
        open_lines, settled_lines, focus_lines = [], [], []
        for issue in issues:
            if issue["issue_id"] in focus_ids:
                focus_lines.append(render_issue_full(issue))
            elif is_terminal(issue["status"]):
                settled_lines.append(
                    f"- {issue['issue_id']} [{issue['status']}] {issue['title']}"
                    + (f" -- {issue['required_action']}" if issue["required_action"] else ""))
            else:
                open_lines.append(render_issue_line(issue))
        body = []
        if focus_lines:
            body.append("ON THE TABLE THIS TURN:\n" + "\n\n".join(focus_lines))
        if open_lines:
            body.append("OTHER OPEN ISSUES:\n" + "\n".join(open_lines))
        if settled_lines:
            body.append("SETTLED (do not reopen without new evidence):\n" + "\n".join(settled_lines))
        return Part("ISSUE LEDGER", "\n\n".join(body), priority=2, floor_tokens=200)

    def _issue_history_part(self, agent_id: str, focus_ids: Sequence[str], round: int) -> Part:
        if not focus_ids:
            return Part("ISSUE HISTORY", "", priority=2, floor_tokens=0)
        chunks: list[str] = []
        visible = {row["id"]: row for row in
                   self.store.visible_messages(self.run_id, agent_id, as_of_round=round)}
        for issue_id in focus_ids:
            lines = []
            for row in visible.values():
                if issue_id in (jload(row["issue_ids"], []) or []):
                    lines.append(render_message(row, max_words=self.message_excerpt_words))
            for position in self.store.positions(self.run_id, issue_id):
                arrow = (f"{position['changed_from']} -> {position['position']}"
                         if position["changed_from"] else position["position"])
                trigger = (f" (triggered by M{position['triggered_by_message_id']})"
                           if position["triggered_by_message_id"] else "")
                lines.append(f"[position] round {position['round']} {position['agent']}: {arrow}"
                             f"{trigger} -- {position['rationale'][:200]}")
            if lines:
                chunks.append(f"--- {issue_id} ---\n" + "\n\n".join(lines))
        return Part("HISTORY OF THE ISSUES ON THE TABLE", "\n\n".join(chunks), priority=2,
                    floor_tokens=400)

    def _recent_part(self, agent_id: str, round: int, exclude_issue_ids: Sequence[str]) -> Part:
        rows = self.store.visible_messages(self.run_id, agent_id, as_of_round=round)
        # Issues that are settled are represented by their one-line summary in the
        # ledger, never by replaying the discussion that settled them.
        settled = {row["issue_id"] for row in self.store.list_issues(self.run_id)
                   if is_terminal(row["status"])}
        skip = set(exclude_issue_ids) | settled
        rendered = []
        for row in rows[-self.recent_messages:]:
            issue_ids = jload(row["issue_ids"], []) or []
            if issue_ids and all(i in skip for i in issue_ids):
                continue  # in the issue history above, or already settled
            rendered.append(render_message(row, max_words=self.message_excerpt_words))
        if not rendered:
            return Part("RECENT ROOM MESSAGES", "", priority=4, floor_tokens=0)
        return Part("RECENT ROOM MESSAGES", "\n\n".join(rendered), priority=3, floor_tokens=200)

    def _own_positions_part(self, agent_id: str, focus_ids: Sequence[str]) -> Part:
        lines = []
        for issue_id in focus_ids:
            position = self.store.latest_position(self.run_id, issue_id, agent_id)
            if position:
                lines.append(f"- {issue_id}: {position}")
        if not lines:
            return Part("YOUR CURRENT POSITIONS", "", priority=2, floor_tokens=0)
        return Part("YOUR CURRENT POSITIONS", "\n".join(lines), priority=1, floor_tokens=60)

    # ------------------------------------------------------------------ build
    def build(self, *, agent_id: str, role: str, phase: str, round: int, directive: str,
              focus_issue_ids: Iterable[str] = (), extra_parts: Sequence[Part] = ()) -> list[ChatMessage]:
        focus_ids = [i for i in focus_issue_ids]
        focus_issues = [row for row in
                        (self.store.get_issue(self.run_id, i) for i in focus_ids) if row]
        header = (f"=== TURN ===\nAGENT: {agent_id}\nROLE: {role}\nPHASE: {phase}\n"
                  f"ROUND: {round}\nISSUES ON THE TABLE: "
                  f"{', '.join(focus_ids) if focus_ids else '(none specified)'}")
        parts: list[Part] = [
            Part("TURN", header, priority=1, floor_tokens=60),
            Part("MANUSCRIPT MAP (ids you may cite)", self.bundle.outline(), priority=2,
                 floor_tokens=100),
            self._manuscript_part(focus_issues, phase),
            self._ledger_part(focus_ids),
            self._issue_history_part(agent_id, focus_ids, round),
            self._own_positions_part(agent_id, focus_ids),
            self._recent_part(agent_id, round, focus_ids),
            *extra_parts,
            Part("YOUR INSTRUCTION THIS TURN", directive, priority=1, floor_tokens=200),
        ]
        parts = [part for part in parts if part.text.strip()]
        body = self._assemble(parts)
        history = self._own_recent_turns(agent_id, round)
        return [*history, ChatMessage("user", body)]

    def _assemble(self, parts: Sequence[Part]) -> str:
        total = sum(part.tokens for part in parts)
        budget = self.max_prompt_tokens
        kept = list(parts)
        if total > budget:
            # Trim lowest-priority parts first, down to their floor, then drop them.
            for priority in sorted({part.priority for part in parts}, reverse=True):
                if total <= budget:
                    break
                for index, part in enumerate(kept):
                    if part.priority != priority or total <= budget:
                        continue
                    excess = total - budget
                    target = max(part.floor_tokens, part.tokens - excess)
                    if target < part.tokens:
                        trimmed = truncate_tokens(part.text, target)
                        total -= part.tokens - estimate_tokens(trimmed)
                        kept[index] = Part(part.name, trimmed, part.priority, part.floor_tokens)
        return "\n\n".join(f"===== {part.name} =====\n{part.text.strip()}" for part in kept)

    def _own_recent_turns(self, agent_id: str, round: int) -> list[ChatMessage]:
        """The agent's own last turns, so it keeps a stable line of argument."""
        if self.own_turn_memory <= 0:
            return []
        rows = self.store.query(
            """SELECT * FROM messages WHERE run_id=? AND sender=? AND round<=?
               ORDER BY id DESC LIMIT ?""",
            (self.run_id, agent_id, round, self.own_turn_memory))
        messages: list[ChatMessage] = []
        for row in reversed(rows):
            messages.append(ChatMessage("user", f"(context for your earlier turn in round {row['round']})"))
            messages.append(ChatMessage("assistant",
                                        truncate_tokens(row["content"], 900)))
        return messages
