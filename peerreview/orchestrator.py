"""Deliberation engine: independent reviews -> consolidation -> shared rounds.

State lives in SQLite, so a run can be interrupted at any point and resumed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import prompts
from .agents import Agent, AgentPool, TurnRequest, TurnResult
from .backends import build_backend
from .config import Config
from .context import ContextBuilder, Part
from .db import ReopenWithoutReason, Store
from .ledger import (LedgerEvent, all_terminal, apply_editor_moves, next_canonical_id,
                     record_position_changes, seed_positions)
from .manuscript import ManuscriptBundle, load_bundle
from .models import (ALL, AUTHOR, EDITOR, REVIEWERS, STATUS_OPEN, AgentSpec, is_terminal)
from .parsing import (extract_issue_ids, extract_mentions, parse_editor_moves, parse_issues,
                      resolve_recipients, strip_blocks)
from .reporting import Reporter
from .util import jload, new_run_id, utcnow, word_count

PHASE_REVIEW = "independent_review"
PHASE_CONSOLIDATION = "consolidation"
PHASE_DELIBERATION = "deliberation"
PHASE_REVISION = "revision"

REVIEWER_TAGS = {"Reviewer1": "R1", "Reviewer2": "R2", "Reviewer3": "R3"}


@dataclass
class RoundOutcome:
    round: int
    position_changes: int = 0
    issues_touched: set[str] = field(default_factory=set)
    terminate: str | None = None


class Orchestrator:
    def __init__(self, config: Config, store: Store, bundle: ManuscriptBundle, run_id: str,
                 pool: AgentPool, reporter: Reporter | None = None):
        self.config = config
        self.store = store
        self.bundle = bundle
        self.run_id = run_id
        self.pool = pool
        self.reporter = reporter or Reporter()
        context_config = config.context
        self.context = ContextBuilder(
            store, run_id, bundle,
            max_prompt_tokens=int(context_config.get("max_prompt_tokens", 60000)),
            recent_messages=int(context_config.get("recent_messages", 14)),
            own_turn_memory=int(context_config.get("own_turn_memory", 3)),
            manuscript_sections_per_issue=int(context_config.get("manuscript_sections_per_issue", 2)),
            message_excerpt_words=int(context_config.get("message_excerpt_words", 260)))
        self.word_limit = int(config.deliberation.get("intervention_word_limit", 350))
        self.max_rounds = int(config.deliberation.get("max_rounds", 20))
        self.stable_rounds_before_stop = int(config.deliberation.get("stable_rounds_before_stop", 2))
        self.max_agenda = int(config.deliberation.get("max_agenda_per_round", 3))
        self.forced_focus: list[str] = []

    # ------------------------------------------------------------------ setup
    @classmethod
    def create(cls, config: Config, *, store: Store | None = None,
               reporter: Reporter | None = None, run_id: str | None = None) -> "Orchestrator":
        inputs = config.manuscript_inputs()
        bundle = load_bundle(manuscript=inputs["manuscript"], appendix=inputs["appendix"],
                             supplementary=inputs["supplementary"], codebook=inputs["codebook"],
                             journal=inputs["journal"])
        store = store or Store(config.db_path)
        specs = config.agent_specs()
        run_id = run_id or new_run_id(config.name)
        store.create_run(run_id=run_id, project=config.name,
                         manuscript=str(inputs["manuscript"]),
                         manuscript_hash=bundle.documents[0].sha256, inputs=bundle.inputs,
                         journal=config.journal, config=config.to_dict(),
                         model_config=config.model_config(),
                         max_rounds=int(config.deliberation.get("max_rounds", 20)))
        for spec in specs.values():
            store.upsert_agent(run_id, spec)
        pool = cls._build_pool(specs, config)
        return cls(config, store, bundle, run_id, pool, reporter)

    @classmethod
    def resume(cls, config: Config, run_id: str, *, store: Store | None = None,
               reporter: Reporter | None = None) -> "Orchestrator":
        store = store or Store(config.db_path)
        run = store.get_run(run_id)
        if run is None:
            raise KeyError(f"run {run_id} not found in {config.db_path}")
        stored_config = Config(data=jload(run["config_json"], config.to_dict()), path=config.path)
        inputs = stored_config.manuscript_inputs()
        bundle = load_bundle(manuscript=inputs["manuscript"], appendix=inputs["appendix"],
                             supplementary=inputs["supplementary"], codebook=inputs["codebook"],
                             journal=stored_config.journal)
        if bundle.documents[0].sha256 != run["manuscript_hash"]:
            raise ValueError(
                "manuscript has changed since this run started "
                f"({run['manuscript_hash'][:12]} -> {bundle.documents[0].sha256[:12]}); "
                "start a new run instead of resuming")
        specs = {}
        for row in store.get_agents(run_id):
            specs[row["id"]] = AgentSpec(
                id=row["id"], role=row["role"], name=row["name"], expertise=row["expertise"] or "",
                system_prompt=row["system_prompt"], provider=row["provider"], model=row["model"],
                params=jload(row["params_json"], {}) or {})
        pool = cls._build_pool(specs, stored_config)
        return cls(stored_config, store, bundle, run_id, pool, reporter)

    @staticmethod
    def _build_pool(specs: dict[str, AgentSpec], config: Config) -> AgentPool:
        agents = {}
        for spec in specs.values():
            backend = build_backend(spec.provider, spec.model, spec.params)
            agents[spec.id] = Agent(spec, backend)
        return AgentPool(agents, isolation=str(config.execution.get("isolation", "thread")),
                         max_workers=int(config.execution.get("max_workers", 5)))

    # ------------------------------------------------------------------ utils
    @property
    def run_row(self) -> sqlite3.Row:
        row = self.store.get_run(self.run_id)
        assert row is not None
        return row

    def _set_phase(self, phase: str, round: int | None = None) -> None:
        fields: dict[str, Any] = {"phase": phase}
        if round is not None:
            fields["current_round"] = round
        self.store.update_run(self.run_id, **fields)

    def _speak(self, result: TurnResult, *, phase: str, round: int, message_type: str,
               recipients: Sequence[str], issue_ids: Sequence[str] = (),
               visibility: str = "shared", meta: dict[str, Any] | None = None) -> int | None:
        """Log an agent turn and post the resulting message to the bus."""
        request = result.request
        agent = self.pool[result.agent_id]
        self.store.log_call(
            self.run_id, agent_id=result.agent_id, round=round, phase=phase,
            provider=agent.backend.provider, model=agent.backend.model,
            params=agent.backend.params, system_prompt=agent.system_prompt,
            messages=[m.to_dict() for m in (request.messages if request else [])],
            response_text=result.text, usage=result.usage, tool_calls=result.tool_calls,
            latency_ms=result.latency_ms, error=result.error)
        if result.error:
            self.reporter.error(f"{result.agent_id} failed: {result.error}")
            self.store.set_agent_status(self.run_id, result.agent_id, "error")
            return None
        text = (result.text or "").strip()
        if not text:
            return None
        prose_words = word_count(strip_blocks(text))
        meta = dict(meta or {})
        meta.update({"prose_words": prose_words, "usage": result.usage})
        if prose_words > self.word_limit * 1.6 and self.config.deliberation.get(
                "enforce_word_limit", True):
            meta["word_limit_exceeded"] = True
            self.reporter.warn(f"{result.agent_id} wrote {prose_words} words of prose "
                               f"(limit {self.word_limit}); recorded in full, flagged in meta")
        mentioned = resolve_recipients(text, recipients)
        message_id = self.store.add_message(
            run_id=self.run_id, round=round, phase=phase, sender=result.agent_id,
            recipients=mentioned, content=text, message_type=message_type,
            issue_ids=list(issue_ids) or extract_issue_ids(text), visibility=visibility, meta=meta)
        self.reporter.turn(result.agent_id, mentioned, issue_ids, text, words=prose_words)
        return message_id

    def _request(self, agent_id: str, *, phase: str, round: int, directive: str,
                 focus_issue_ids: Sequence[str] = (), extra_parts: Sequence[Part] = ()) -> TurnRequest:
        agent = self.pool[agent_id]
        messages = self.context.build(agent_id=agent_id, role=agent.role, phase=phase, round=round,
                                      directive=directive, focus_issue_ids=focus_issue_ids,
                                      extra_parts=extra_parts)
        return TurnRequest(agent_id=agent_id, phase=phase, round=round, messages=messages,
                           focus_issue_ids=list(focus_issue_ids))

    def _is_silent(self, text: str) -> bool:
        stripped = strip_blocks(text).strip().upper()
        return stripped.startswith("NO FURTHER COMMENT") and len(stripped) < 120

    # ---------------------------------------------------------------- phase 1
    def phase1_independent_reviews(self) -> None:
        if self.store.query_one(
                "SELECT 1 FROM messages WHERE run_id=? AND phase=? LIMIT 1",
                (self.run_id, PHASE_REVIEW)):
            self.reporter.info("independent reviews already present; skipping phase 1")
            return
        self.reporter.phase("Phase 1: independent reviews (reviewers cannot see each other)")
        self._set_phase(PHASE_REVIEW, 0)
        directive = prompts.PHASE1_REVIEWER
        requests = [self._request(name, phase=PHASE_REVIEW, round=0, directive=directive)
                    for name in REVIEWERS]
        results = self.pool.run_many(requests)
        for result in results:
            # Private: addressed to the Editor only. Other reviewers cannot read it.
            message_id = self._speak(result, phase=PHASE_REVIEW, round=0,
                                     message_type="independent_review", recipients=[EDITOR],
                                     visibility="private")
            if message_id is None:
                continue
            issues = parse_issues(result.text)
            tag = REVIEWER_TAGS.get(result.agent_id, result.agent_id)
            for index, issue in enumerate(issues, start=1):
                issue_id = f"{tag}-{index:02d}"
                self.store.create_issue(
                    self.run_id, issue_id=issue_id, title=issue.title, actor=result.agent_id,
                    round=0, description=issue.description, category=issue.category,
                    severity=issue.severity, status=STATUS_OPEN, raised_by=[result.agent_id],
                    manuscript_location=issue.location, original_comment=issue.raw,
                    current_summary=issue.claim, provisional=True, confidence=issue.confidence,
                    message_id=message_id)
            uncited = [issue.title for issue in issues if not issue.location.strip()]
            if uncited:
                self.reporter.warn(f"{result.agent_id} raised {len(uncited)} issue(s) with no "
                                   f"manuscript location: {'; '.join(uncited)[:160]}")
            self.reporter.info(f"{result.agent_id} raised {len(issues)} issue(s)")
            self.store.set_agent_status(self.run_id, result.agent_id, "done")

    # ---------------------------------------------------------------- phase 2
    def phase2_consolidation(self) -> None:
        if self.store.list_issues(self.run_id, provisional=False):
            self.reporter.info("canonical ledger already present; skipping consolidation")
            return
        self.reporter.phase("Phase 2: issue consolidation")
        self._set_phase(PHASE_CONSOLIDATION, 0)
        provisional = self.store.list_issues(self.run_id, provisional=True, include_merged=True)
        if not provisional:
            self.reporter.warn("no provisional issues were produced by the reviewers")
        listing = "\n\n".join(
            f"{row['issue_id']} (raised by {', '.join(jload(row['raised_by'], []) or [])})\n"
            f"  title: {row['title']}\n  category: {row['category']}  severity: {row['severity']}\n"
            f"  location: {row['manuscript_location']}\n  claim: {row['current_summary']}"
            for row in provisional)
        extra = [Part("PROVISIONAL ISSUES FROM THE INDEPENDENT REVIEWS", listing, priority=1,
                      floor_tokens=1200)]
        request = self._request(EDITOR, phase=PHASE_CONSOLIDATION, round=0,
                                directive=prompts.CONSOLIDATION_EDITOR, extra_parts=extra)
        result = self.pool.run_one(request)
        message_id = self._speak(result, phase=PHASE_CONSOLIDATION, round=0,
                                 message_type="consolidation", recipients=[ALL])
        moves = parse_editor_moves(result.text or "")
        lookup = {row["issue_id"].upper(): row["issue_id"] for row in provisional}
        events = apply_editor_moves(self.store, self.run_id, round=0, moves=moves,
                                    message_id=message_id or 0, provisional_lookup=lookup)
        for event in events:
            self.reporter.event(str(event))
        if not self.store.list_issues(self.run_id, provisional=False):
            self._promote_provisional_issues(message_id or 0)
        self._absorb_unmerged_provisionals(message_id or 0)
        self._seed_initial_positions()
        canonical = self.store.list_issues(self.run_id, provisional=False)
        self.reporter.info(f"canonical ledger: {len(canonical)} issue(s)")

    def _promote_provisional_issues(self, message_id: int) -> None:
        """Fallback when the Editor produced no canonical issues."""
        self.reporter.warn("editor produced no canonical issues; promoting provisional issues")
        for row in self.store.list_issues(self.run_id, provisional=True):
            issue_id = next_canonical_id(self.store, self.run_id)
            self.store.create_issue(
                self.run_id, issue_id=issue_id, title=row["title"], actor=EDITOR, round=0,
                description=row["description"], category=row["category"], severity=row["severity"],
                status=STATUS_OPEN, raised_by=jload(row["raised_by"], []) or [],
                manuscript_location=row["manuscript_location"],
                original_comment=row["original_comment"], current_summary=row["current_summary"],
                message_id=message_id)
            self.store.merge_issue(self.run_id, source_id=row["issue_id"], target_id=issue_id,
                                   actor=EDITOR, round=0, reason="promoted verbatim",
                                   message_id=message_id)

    def _absorb_unmerged_provisionals(self, message_id: int) -> None:
        """A minority concern must not vanish because the Editor forgot to merge it."""
        canonical = self.store.list_issues(self.run_id, provisional=False)
        for row in self.store.list_issues(self.run_id, provisional=True):
            if row["merged_into"]:
                continue
            match = self._best_match(row, canonical)
            if match is not None:
                self.store.merge_issue(self.run_id, source_id=row["issue_id"],
                                       target_id=match["issue_id"], actor=EDITOR, round=0,
                                       reason="auto-linked: unmerged provisional issue",
                                       message_id=message_id)
                continue
            issue_id = next_canonical_id(self.store, self.run_id)
            self.store.create_issue(
                self.run_id, issue_id=issue_id, title=row["title"], actor=EDITOR, round=0,
                description=row["description"], category=row["category"], severity=row["severity"],
                status=STATUS_OPEN, raised_by=jload(row["raised_by"], []) or [],
                manuscript_location=row["manuscript_location"],
                original_comment=row["original_comment"],
                current_summary=row["current_summary"], message_id=message_id)
            self.store.merge_issue(self.run_id, source_id=row["issue_id"], target_id=issue_id,
                                   actor=EDITOR, round=0,
                                   reason="carried forward: not consolidated by the editor",
                                   message_id=message_id)
            self.reporter.event(f"carried forward unconsolidated {row['issue_id']} -> {issue_id}")
            canonical = self.store.list_issues(self.run_id, provisional=False)

    @staticmethod
    def _best_match(row: sqlite3.Row, canonical: Sequence[sqlite3.Row]) -> sqlite3.Row | None:
        import re

        def tokens(text: str) -> set[str]:
            return {t for t in re.findall(r"[a-z]{4,}", (text or "").lower())}

        source = tokens(row["title"]) | tokens(row["current_summary"])
        best, best_score = None, 0.0
        for candidate in canonical:
            target = tokens(candidate["title"]) | tokens(candidate["current_summary"])
            if not source or not target:
                continue
            overlap = len(source & target) / min(len(source), len(target))
            if overlap > best_score:
                best, best_score = candidate, overlap
        return best if best_score >= 0.55 else None

    def _seed_initial_positions(self) -> None:
        for row in self.store.list_issues(self.run_id, provisional=False):
            raised = [a for a in (jload(row["raised_by"], []) or []) if a in REVIEWERS]
            seed_positions(self.store, self.run_id, row["issue_id"], raised, round=0,
                           position=f"OBJECTION ({(row['severity'] or 'moderate').upper()})")

    # ---------------------------------------------------------------- phase 3
    def open_deliberation(self) -> None:
        disclosed = self.store.disclose_messages(self.run_id, 1, phase=PHASE_REVIEW)
        if disclosed:
            self.reporter.info(f"disclosed {disclosed} independent review(s) to all participants")
        if self.store.query_one(
                """SELECT 1 FROM messages WHERE run_id=? AND phase=? AND message_type='system'
                   LIMIT 1""", (self.run_id, PHASE_DELIBERATION)):
            return  # already opened; resuming
        self.store.add_message(
            run_id=self.run_id, round=1, phase=PHASE_DELIBERATION, sender=EDITOR,
            recipients=[ALL], message_type="system",
            content=("Shared deliberation is open. All independent reviews and the issue ledger "
                     "are now visible to every participant. Discussion is issue-centric: cite "
                     "ISSUE ids and manuscript locations."))

    def phase3_deliberation(self) -> str:
        self.reporter.phase("Phase 3: shared deliberation")
        self.open_deliberation()
        # Resume after the last round that finished, not the one in progress.
        start_round = max(1, int(self.run_row["completed_round"] or 0) + 1)
        stable_rounds = 0
        termination = None
        for round_number in range(start_round, self.max_rounds + 1):
            self._set_phase(PHASE_DELIBERATION, round_number)
            self.reporter.round_header(round_number)
            self._apply_interventions(round_number)
            if self.store.run_status(self.run_id) == "paused":
                return "paused by human intervention"
            if all_terminal(self.store, self.run_id):
                return "all issues reached terminal statuses"
            outcome = self._run_round(round_number)
            self.store.update_run(self.run_id, completed_round=round_number)
            if outcome.terminate:
                return outcome.terminate
            stable_rounds = 0 if outcome.position_changes else stable_rounds + 1
            if all_terminal(self.store, self.run_id):
                return "all issues reached terminal statuses"
            if stable_rounds >= self.stable_rounds_before_stop:
                return (f"no substantive position change for {stable_rounds} consecutive rounds")
        return f"max_rounds ({self.max_rounds}) reached"

    def _run_round(self, round_number: int) -> RoundOutcome:
        outcome = RoundOutcome(round=round_number)
        if round_number == 1 and self.config.deliberation.get("author_first_response", True):
            self._author_opening(round_number, outcome)

        agenda = self._editor_opens_round(round_number, outcome)
        if outcome.terminate:
            return outcome
        if not agenda:
            open_issues = [row["issue_id"] for row in self.store.open_issues(self.run_id)]
            if not open_issues:
                outcome.terminate = "no open issues remain"
                return outcome
            agenda = [{"issue_ids": open_issues[:1], "speakers": list(REVIEWERS) + [AUTHOR],
                       "question": "State your current position and what, if anything, would change it."}]
        for item in agenda[: self.max_agenda]:
            self._run_agenda_item(round_number, item, outcome)
        self._editor_closes_round(round_number, outcome)
        return outcome

    def _author_opening(self, round_number: int, outcome: RoundOutcome) -> None:
        open_issues = [row["issue_id"] for row in self.store.open_issues(self.run_id)]
        if not open_issues:
            return
        directive = prompts.AUTHOR_FIRST_RESPONSE.format(word_limit=self.word_limit * 2)
        request = self._request(AUTHOR, phase=PHASE_DELIBERATION, round=round_number,
                                directive=directive, focus_issue_ids=open_issues)
        result = self.pool.run_one(request)
        message_id = self._speak(result, phase=PHASE_DELIBERATION, round=round_number,
                                 message_type="author_response", recipients=[ALL],
                                 issue_ids=open_issues)
        if message_id:
            self._record_positions(round_number, AUTHOR, result.text, message_id, open_issues,
                                   outcome)

    def _editor_opens_round(self, round_number: int, outcome: RoundOutcome) -> list[dict[str, Any]]:
        open_issues = [row["issue_id"] for row in self.store.open_issues(self.run_id)]
        focus = self.forced_focus or open_issues
        directive = prompts.EDITOR_ROUND_OPEN.format(round=round_number, max_agenda=self.max_agenda)
        if self.forced_focus:
            directive += ("\n\nThe human editor-in-chief has asked you to prioritise: "
                          + ", ".join(self.forced_focus))
        request = self._request(EDITOR, phase=PHASE_DELIBERATION, round=round_number,
                                directive=directive, focus_issue_ids=focus[:6])
        result = self.pool.run_one(request)
        message_id = self._speak(result, phase=PHASE_DELIBERATION, round=round_number,
                                 message_type="agenda", recipients=[ALL])
        moves = parse_editor_moves(result.text or "")
        if message_id:
            events = apply_editor_moves(self.store, self.run_id, round=round_number, moves=moves,
                                        message_id=message_id)
            self._report_ledger(events, outcome)
        if moves.terminate:
            outcome.terminate = f"editor ended deliberation: {moves.terminate}"
        self.forced_focus = []
        agenda = [item for item in moves.agenda if item.get("issue_ids")]
        for item in agenda:
            item["issue_ids"] = [i for i in item["issue_ids"]
                                 if self.store.get_issue(self.run_id, i) is not None]
        return [item for item in agenda if item["issue_ids"]]

    def _run_agenda_item(self, round_number: int, item: dict[str, Any],
                         outcome: RoundOutcome) -> None:
        issue_ids = item["issue_ids"]
        live = []
        for issue_id in issue_ids:
            issue = self.store.get_issue(self.run_id, issue_id)
            if issue is not None and not is_terminal(issue["status"]):
                live.append(issue_id)
        if not live:
            return
        outcome.issues_touched.update(live)
        self.reporter.event(f"Editor opens {', '.join(live)}")
        speakers = [s for s in item.get("speakers", []) if self.pool.get(s)] or list(REVIEWERS)
        question = item.get("question", "")
        spoken: list[str] = []
        pending = list(dict.fromkeys(speakers))
        replies_left = 2
        while pending:
            speaker = pending.pop(0)
            if speaker in spoken or speaker == EDITOR:
                continue
            directive = prompts.DELIBERATION_TURN.format(
                round=round_number, word_limit=self.word_limit,
                directive=(f"The Editor has called on you regarding {', '.join(live)}.\n"
                           f"Question from the chair: {question}"
                           if speaker in speakers else
                           f"You were addressed directly about {', '.join(live)}. Reply only if "
                           f"you have something new to say."))
            request = self._request(speaker, phase=PHASE_DELIBERATION, round=round_number,
                                    directive=directive, focus_issue_ids=live)
            result = self.pool.run_one(request)
            spoken.append(speaker)
            if result.error or not (result.text or "").strip():
                continue
            if self._is_silent(result.text):
                self.reporter.event(f"{speaker}: no further comment")
                continue
            message_type = "author_response" if speaker == AUTHOR else "deliberation"
            message_id = self._speak(result, phase=PHASE_DELIBERATION, round=round_number,
                                     message_type=message_type, recipients=[ALL], issue_ids=live)
            if message_id is None:
                continue
            self._record_positions(round_number, speaker, result.text, message_id, live, outcome)
            # Someone addressed by name gets a right of reply, bounded.
            if replies_left > 0:
                for mention in extract_mentions(result.text):
                    if (mention in self.pool.agents and mention not in spoken
                            and mention not in pending and mention != EDITOR):
                        pending.append(mention)
                        replies_left -= 1
                        if replies_left <= 0:
                            break
        still_open = [i for i in live
                      if not is_terminal(self.store.get_issue(self.run_id, i)["status"])]
        if AUTHOR not in spoken and still_open:
            directive = prompts.DELIBERATION_TURN.format(
                round=round_number, word_limit=self.word_limit,
                directive=(f"Respond to what was said this round about {', '.join(live)}. "
                           "Accept, reject or correct each point, and cite where the manuscript "
                           "already speaks to it."))
            request = self._request(AUTHOR, phase=PHASE_DELIBERATION, round=round_number,
                                    directive=directive, focus_issue_ids=live)
            result = self.pool.run_one(request)
            if not result.error and result.text.strip() and not self._is_silent(result.text):
                message_id = self._speak(result, phase=PHASE_DELIBERATION, round=round_number,
                                         message_type="author_response", recipients=[ALL],
                                         issue_ids=live)
                if message_id:
                    self._record_positions(round_number, AUTHOR, result.text, message_id, live,
                                           outcome)

    def _editor_closes_round(self, round_number: int, outcome: RoundOutcome) -> None:
        focus = sorted(outcome.issues_touched) or [
            row["issue_id"] for row in self.store.open_issues(self.run_id)][:4]
        if not focus:
            return
        directive = prompts.EDITOR_ROUND_CLOSE.format(round=round_number)
        request = self._request(EDITOR, phase=PHASE_DELIBERATION, round=round_number,
                                directive=directive, focus_issue_ids=focus)
        result = self.pool.run_one(request)
        message_id = self._speak(result, phase=PHASE_DELIBERATION, round=round_number,
                                 message_type="ledger", recipients=[ALL], issue_ids=focus)
        moves = parse_editor_moves(result.text or "")
        if message_id:
            events = apply_editor_moves(self.store, self.run_id, round=round_number, moves=moves,
                                        message_id=message_id)
            self._report_ledger(events, outcome)
        if moves.terminate and not outcome.terminate:
            outcome.terminate = f"editor ended deliberation: {moves.terminate}"

    def _record_positions(self, round_number: int, agent_id: str, text: str, message_id: int,
                          issue_ids: Sequence[str], outcome: RoundOutcome) -> None:
        events = record_position_changes(self.store, self.run_id, round=round_number,
                                         agent_id=agent_id, text=text, message_id=message_id,
                                         known_issue_ids=list(issue_ids))
        for event in events:
            outcome.position_changes += 1
            outcome.issues_touched.add(event.issue_id or "")
            trigger = event.payload.get("triggered_by_agent")
            trigger_id = event.payload.get("triggered_by_message_id")
            label = (f"M{trigger_id} {trigger}" if trigger_id else trigger) or None
            self.reporter.position(agent_id, event.issue_id or "?", event.payload.get("from"),
                                   event.payload.get("to", ""), label)

    def _report_ledger(self, events: Iterable[LedgerEvent], outcome: RoundOutcome) -> None:
        for event in events:
            if event.issue_id:
                outcome.issues_touched.add(event.issue_id)
            if event.kind in {"status", "update", "reopen"}:
                self.reporter.ledger(event.issue_id or "?", event.detail)
            else:
                self.reporter.event(str(event))

    # --------------------------------------------------------- interventions
    def _apply_interventions(self, round_number: int) -> None:
        for row in self.store.pending_interventions(self.run_id):
            payload = jload(row["payload_json"], {}) or {}
            kind = row["kind"]
            if kind == "pause":
                self.store.update_run(self.run_id, status="paused")
                self.reporter.event("human intervention: run paused")
            elif kind in {"message", "correction", "evidence"}:
                speaker = row["speak_as"] or "HUMAN"
                label = {"message": "human intervention", "correction": "human correction",
                         "evidence": "human evidence"}[kind]
                content = (f"[{label.upper()} - speaking as {speaker}. This is the real human "
                           f"editor-in-chief or author; treat it as authoritative.]\n\n"
                           + payload.get("text", ""))
                message_id = self.store.add_message(
                    run_id=self.run_id, round=round_number, phase=PHASE_DELIBERATION,
                    sender=f"HUMAN({speaker})", recipients=[ALL], content=content,
                    message_type="human_intervention",
                    issue_ids=[row["issue_id"]] if row["issue_id"] else
                              extract_issue_ids(payload.get("text", "")),
                    is_human=True, meta={"intervention_id": row["id"], "kind": kind})
                self.store.link_intervention_message(self.run_id, row["id"], message_id)
                self.reporter.event(f"human intervention inserted as M{message_id}")
            elif kind == "reopen":
                try:
                    self.store.update_issue(self.run_id, row["issue_id"], actor="HUMAN",
                                            round=round_number, reason=payload.get("reason", ""),
                                            allow_reopen=True, status=STATUS_OPEN, closed_round=None)
                    self.reporter.event(f"human reopened {row['issue_id']}")
                except (ReopenWithoutReason, KeyError) as exc:
                    self.reporter.warn(f"could not reopen {row['issue_id']}: {exc}")
            elif kind == "focus":
                ids = payload.get("issue_ids") or ([row["issue_id"]] if row["issue_id"] else [])
                self.forced_focus = [i for i in ids if self.store.get_issue(self.run_id, i)]
                self.reporter.event(f"human focus: {', '.join(self.forced_focus) or '(none)'}")
            elif kind == "resume":
                self.store.update_run(self.run_id, status="running")
            self.store.consume_intervention(self.run_id, row["id"])

    # ------------------------------------------------------------------- run
    def run(self, *, resume: bool = False) -> str:
        self.store.update_run(self.run_id, status="running")
        try:
            self.phase1_independent_reviews()
            self.phase2_consolidation()
            reason = self.phase3_deliberation()
        except KeyboardInterrupt:
            self.store.update_run(self.run_id, status="paused",
                                  termination_reason="interrupted by user")
            self.reporter.warn("interrupted; run state is saved and resumable")
            raise
        except Exception as exc:
            self.store.update_run(self.run_id, status="failed", ended_at=utcnow(),
                                  termination_reason=f"error: {exc}")
            raise
        status = "paused" if self.store.run_status(self.run_id) == "paused" else "finished"
        self.store.update_run(self.run_id, status=status, ended_at=utcnow(),
                              termination_reason=reason, phase="export")
        self.reporter.phase(f"Deliberation ended: {reason}")
        return reason
