"""Run outputs: reviews, ledgers, transcript, revision plan, influence report, JSON."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from .db import Store
from .influence import influence_report, issue_influence, narrate_issue
from .models import (AUTHOR, DISMISSED_STATUSES, EDITOR, REVIEWERS, STATUS_NEW_ANALYSIS,
                     STATUS_ROBUSTNESS_OPTIONAL, STATUS_SUBSTANTIVE, STATUS_TO_CLASSIFICATION,
                     STATUS_UNRESOLVED, is_terminal)
from .parsing import parse_revisions
from .util import jload, slugify

FILES = {
    "initial_reviews": "01_initial_reviews.md",
    "ledger_initial": "02_issue_ledger_initial.md",
    "transcript": "03_deliberation_transcript.md",
    "ledger_final": "04_issue_ledger_final.md",
    "revision_plan": "05_revision_plan.md",
    "unresolved": "06_unresolved_disagreements.md",
    "influence": "07_influence_report.md",
    "json": "08_machine_readable_results.json",
    "revised_manuscript": "09_revised_manuscript.md",
    "response_letter": "10_response_to_reviewers.md",
    "dashboard": "11_dashboard.html",
}

PRIORITY_BY_SEVERITY = {"major": "essential", "moderate": "important", "minor": "minor"}
PRIORITY_ORDER = {"essential": 0, "important": 1, "minor": 2}


# --------------------------------------------------------------------- helpers
def run_directory(runs_dir: Path, run: sqlite3.Row) -> Path:
    stamp = (run["started_at"] or "")[:10] or "undated"
    base = Path(runs_dir) / f"{stamp}-{slugify(run['project'])}"
    marker = base / ".run_id"
    if base.exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() != run["run_id"]:
        base = Path(runs_dir) / f"{stamp}-{slugify(run['project'])}-{run['run_id'][-6:]}"
    base.mkdir(parents=True, exist_ok=True)
    (base / ".run_id").write_text(run["run_id"], encoding="utf-8")
    return base


def _agents_of(issue: sqlite3.Row) -> list[str]:
    return jload(issue["raised_by"], []) or []


def ledger_snapshot(store: Store, run_id: str, as_of_round: int) -> list[dict[str, Any]]:
    """Reconstruct the ledger as it stood at the end of `as_of_round`.

    Works by rolling the current projection backwards through the append-only
    `issue_events` audit, so the "initial" ledger shows what the participants
    actually saw rather than the final state of each field.
    """
    state: dict[str, dict[str, Any]] = {
        row["issue_id"]: {key: row[key] for key in row.keys()}
        for row in store.list_issues(run_id, provisional=None, include_merged=True)}
    for event in reversed(store.issue_events(run_id)):
        if event["round"] <= as_of_round:
            continue
        issue_id = event["issue_id"]
        if issue_id not in state:
            continue
        payload = jload(event["payload_json"], {}) or {}
        if event["event_type"] == "created":
            state.pop(issue_id, None)          # did not exist yet
            continue
        for key, value in (payload.get("before") or {}).items():
            if key in state[issue_id]:
                state[issue_id][key] = value
        if event["event_type"] == "merge" and payload.get("merged_into"):
            state[issue_id]["merged_into"] = None
    snapshot = []
    for issue in state.values():
        if issue["provisional"] or issue["merged_into"]:
            continue
        raised = issue["raised_by"]
        issue["raised_by"] = jload(raised, []) or [] if isinstance(raised, str) else (raised or [])
        issue["required_action"] = issue["required_action"] or ""
        snapshot.append(issue)
    return snapshot


def latest_positions(store: Store, run_id: str, issue_id: str) -> dict[str, str]:
    positions: dict[str, str] = {}
    for row in store.positions(run_id, issue_id):
        positions[row["agent"]] = row["position"]
    return positions


def author_revisions(store: Store, run_id: str) -> dict[str, list[Any]]:
    """Author REVISION blocks, keyed by issue id."""
    by_issue: dict[str, list[Any]] = {}
    for row in store.all_messages(run_id):
        if row["sender"] != AUTHOR:
            continue
        for revision in parse_revisions(row["content"]):
            if revision.issue_id:
                by_issue.setdefault(revision.issue_id, []).append(revision)
    return by_issue


def issue_priority(issue: sqlite3.Row) -> str:
    return issue["priority"] or PRIORITY_BY_SEVERITY.get(issue["severity"] or "moderate", "important")


def classification(issue: sqlite3.Row) -> str:
    return STATUS_TO_CLASSIFICATION.get(issue["status"], "unresolved disagreement")


# ----------------------------------------------------------------- 01 reviews
def render_initial_reviews(store: Store, run_id: str) -> str:
    run = store.get_run(run_id)
    lines = [f"# Initial independent reviews", "",
             f"Run: `{run_id}`  ", f"Manuscript: `{run['manuscript']}`  ",
             f"Journal: {run['journal'] or 'unspecified'}  ",
             "",
             "Reviewers wrote these without sight of one another's assessments.", ""]
    for row in store.all_messages(run_id):
        if row["message_type"] != "independent_review":
            continue
        lines.append(f"## {row['sender']} (message M{row['id']})")
        lines.append("")
        lines.append(row["content"].strip())
        lines.append("")
        provisional = [i for i in store.list_issues(run_id, provisional=True, include_merged=True)
                       if row["sender"] in _agents_of(i)]
        if provisional:
            lines.append(f"### Issues raised by {row['sender']}")
            for issue in provisional:
                target = f" -> consolidated into {issue['merged_into']}" if issue["merged_into"] else ""
                lines.append(f"- **{issue['issue_id']}** {issue['title']} "
                             f"({issue['severity']}, {issue['category']}){target}")
            lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ 02/04 ledgers
def render_ledger(store: Store, run_id: str, *, initial: bool = False) -> str:
    run = store.get_run(run_id)
    title = "Initial issue ledger" if initial else "Final issue ledger"
    lines = [f"# {title}", "", f"Run: `{run_id}`  ",
             f"Rounds completed: {run['current_round']}  ",
             f"Termination: {run['termination_reason'] or 'n/a'}  ", ""]
    if initial:
        snapshot = ledger_snapshot(store, run_id, as_of_round=0)
        for issue in sorted(snapshot, key=lambda i: i["issue_id"]):
            lines += [
                f"## {issue['issue_id']}",
                f"**Title:** {issue['title']}", "",
                f"**Raised by:** {', '.join(issue['raised_by']) or 'n/a'}  ",
                f"**Category:** {issue['category']}  ",
                f"**Initial severity:** {issue['severity']}  ",
                f"**Status:** {issue['status']}  ",
                f"**Manuscript location:** {issue['manuscript_location'] or 'n/a'}", "",
                "**Editor synthesis:**", "", (issue["current_summary"] or "(none)").strip(), "",
            ]
            if issue["required_action"]:
                lines += ["**Question for Author:**", "",
                          issue["required_action"].replace("Answer: ", "").strip(), ""]
            merged = [row["issue_id"] for row in
                      store.list_issues(run_id, provisional=True, include_merged=True)
                      if row["merged_into"] == issue["issue_id"]]
            if merged:
                lines += [f"**Consolidated from:** {', '.join(merged)}", ""]
        return "\n".join(lines)

    for issue in store.list_issues(run_id):
        positions = latest_positions(store, run_id, issue["issue_id"])
        lines += [
            f"## {issue['issue_id']} - {issue['title']}", "",
            f"**Status:** {issue['status']}  ",
            f"**Severity:** {issue['severity'] or 'n/a'}  ",
            f"**Category:** {issue['category'] or 'n/a'}  ",
            f"**Priority:** {issue_priority(issue)}  ",
            f"**Raised by:** {', '.join(_agents_of(issue)) or 'n/a'}  ",
            f"**Manuscript location:** {issue['manuscript_location'] or 'n/a'}  ",
            f"**Opened round {issue['created_round']}"
            + (f", closed round {issue['closed_round']}" if issue["closed_round"] is not None else "")
            + "**", "",
            "**Current summary:**", "", (issue["current_summary"] or "(none)").strip(), "",
        ]
        if issue["required_action"]:
            lines += ["**Required action:**", "", issue["required_action"].strip(), ""]
        if issue["editor_decision"]:
            lines += ["**Editor decision:**", "", issue["editor_decision"].strip(), ""]
        if positions:
            lines += ["**Final positions:**", ""]
            lines += [f"- {agent}: {position}" for agent, position in sorted(positions.items())]
            lines += [""]
    return "\n".join(lines)


# --------------------------------------------------------------- 03 transcript
def render_transcript(store: Store, run_id: str) -> str:
    lines = ["# Deliberation transcript", "",
             "Message ids (`M12`) are the identifiers agents cite when recording what "
             "changed their position.", ""]
    current_round = None
    for row in store.all_messages(run_id):
        if row["round"] != current_round:
            current_round = row["round"]
            lines += ["", f"## Round {current_round}", ""]
        recipients = ", ".join(jload(row["recipients"], []) or [])
        issue_ids = ", ".join(jload(row["issue_ids"], []) or [])
        flags = []
        if row["visibility"] == "private":
            flags.append(f"private to {recipients}"
                         + (f", disclosed at round {row['disclosed_at_round']}"
                            if row["disclosed_at_round"] is not None else ", never disclosed"))
        if row["is_human"]:
            flags.append("HUMAN INTERVENTION")
        header = f"### [M{row['id']}] {row['sender']} -> {recipients}"
        if issue_ids:
            header += f"  ({issue_ids})"
        lines.append(header)
        lines.append(f"*{row['timestamp']} - {row['message_type']}"
                     + (f" - {'; '.join(flags)}" if flags else "") + "*")
        lines.append("")
        lines.append(row["content"].strip())
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------- 05 revision plan
def _analysis_requirement(issue: sqlite3.Row, revisions: Sequence[Any]) -> tuple[str, str]:
    explicit = ""
    for revision in revisions:
        if revision.analysis and revision.analysis.strip().lower() not in {"none", "n/a", "no"}:
            explicit = revision.analysis.strip()
            break
    if issue["status"] == STATUS_NEW_ANALYSIS:
        return "yes", explicit or (issue["required_action"] or "").strip() or "see required action"
    if issue["status"] == STATUS_ROBUSTNESS_OPTIONAL:
        return "optional", explicit or (issue["required_action"] or "").strip()
    return ("yes", explicit) if explicit else ("no", "")


def _textual_requirement(issue: sqlite3.Row, revisions: Sequence[Any]) -> tuple[str, str]:
    for revision in revisions:
        if revision.change and revision.change.strip().lower() not in {"none", "n/a"}:
            return "yes", revision.change.strip()
    if issue["status"] in {STATUS_SUBSTANTIVE} or classification(issue) in {
            "substantive revision", "clarification", "exposition"}:
        return "yes", (issue["required_action"] or "").strip() or "see required action"
    return "no", ""


def build_revision_plan(store: Store, run_id: str) -> list[dict[str, Any]]:
    revisions = author_revisions(store, run_id)
    plan: list[dict[str, Any]] = []
    for issue in store.list_issues(run_id):
        issue_id = issue["issue_id"]
        positions = latest_positions(store, run_id, issue_id)
        issue_revisions = revisions.get(issue_id, [])
        analysis_required, analysis_detail = _analysis_requirement(issue, issue_revisions)
        textual_required, textual_detail = _textual_requirement(issue, issue_revisions)
        influence = issue_influence(store, run_id, issue_id)
        plan.append({
            "issue_id": issue_id,
            "title": issue["title"],
            "priority": issue_priority(issue),
            "classification": classification(issue),
            "status": issue["status"],
            "severity": issue["severity"],
            "category": issue["category"],
            "dismissed": issue["status"] in DISMISSED_STATUSES,
            "problem": (issue["description"] or issue["current_summary"] or "").strip(),
            "why_it_matters": _why_it_matters(issue),
            "deliberation_summary": (issue["current_summary"] or "").strip(),
            "manuscript_location": issue["manuscript_location"] or "",
            "required_action": (issue["required_action"] or "").strip(),
            "analysis_required": analysis_required,
            "analysis_detail": analysis_detail,
            "textual_revision_required": textual_required,
            "textual_detail": textual_detail,
            "reviewer_positions": {name: positions.get(name, "no position recorded")
                                   for name in REVIEWERS},
            "author_position": positions.get(AUTHOR, _author_stance(issue_revisions)),
            "editor_decision": (issue["editor_decision"] or "").strip(),
            "confidence": issue["confidence"] or "medium",
            "influence_path": influence.path_string,
            "raised_by": _agents_of(issue),
        })
    plan.sort(key=lambda item: (PRIORITY_ORDER.get(item["priority"], 3), item["issue_id"]))
    return plan


def _extract_why(text: str) -> str:
    for marker in ("WHY IT MATTERS:", "WHY THIS MATTERS:"):
        if marker in text:
            tail = text.split(marker, 1)[1]
            for stop in ("PROPOSED REMEDY:", "WHAT WOULD RESOLVE", "MANUSCRIPT LOCATION:",
                         "CONFIDENCE:", "<<<"):
                tail = tail.split(stop, 1)[0]
            return tail.strip()
    return ""


def _why_it_matters(issue: sqlite3.Row) -> str:
    """Prefer the reviewer's own statement, preserved verbatim through merging."""
    for source in (issue["original_comment"] or "", issue["description"] or ""):
        why = _extract_why(source)
        if why:
            return why
    return (issue["description"] or issue["current_summary"] or "").strip()


def _author_stance(revisions: Sequence[Any]) -> str:
    if not revisions:
        return "no position recorded"
    kinds = {revision.kind for revision in revisions}
    return "proposed: " + ", ".join(sorted(kinds))


def render_revision_plan(store: Store, run_id: str) -> str:
    run = store.get_run(run_id)
    plan = build_revision_plan(store, run_id)
    lines = ["# Revision plan", "",
             f"Run: `{run_id}`  ", f"Manuscript: `{run['manuscript']}`  ",
             f"Journal: {run['journal'] or 'unspecified'}  ",
             f"Rounds: {run['current_round']}  ",
             f"Termination: {run['termination_reason'] or 'n/a'}", "",
             "This is an operational plan, not a referee report. Items are ordered by "
             "priority; every issue from the deliberation appears exactly once.", ""]
    counts: dict[str, int] = {}
    for item in plan:
        counts[item["classification"]] = counts.get(item["classification"], 0) + 1
    lines += ["## Summary", ""]
    lines += [f"- {classification_name}: {count}"
              for classification_name, count in sorted(counts.items())]
    lines += [""]

    for priority in ("essential", "important", "minor"):
        group = [item for item in plan if item["priority"] == priority and not item["dismissed"]]
        if not group:
            continue
        lines += [f"## Priority: {priority}", ""]
        for item in group:
            lines += _render_plan_item(item)
    dismissed = [item for item in plan if item["dismissed"]]
    if dismissed:
        lines += ["## No change required (rebutted, withdrawn, or out of scope)", ""]
        for item in dismissed:
            lines += _render_plan_item(item)
    return "\n".join(lines)


def _render_plan_item(item: dict[str, Any]) -> list[str]:
    lines = [
        f"### {item['issue_id']} - {item['title']}", "",
        f"**PRIORITY:** {item['priority']}  ",
        f"**FINAL CLASSIFICATION:** {item['classification']}  ",
        f"**STATUS:** {item['status']}  ",
        f"**SEVERITY:** {item['severity'] or 'n/a'}  ",
        f"**RAISED BY:** {', '.join(item['raised_by']) or 'n/a'}", "",
        "**PROBLEM:**", "", item["problem"] or "(not stated)", "",
        "**WHY IT MATTERS:**", "", item["why_it_matters"] or "(not stated)", "",
        "**FINAL DELIBERATION SUMMARY:**", "", item["deliberation_summary"] or "(none)", "",
        f"**MANUSCRIPT LOCATION:** {item['manuscript_location'] or 'n/a'}", "",
        "**EXACT REQUIRED ACTION:**", "",
        item["required_action"] or ("No change required." if item["dismissed"]
                                    else "(editor recorded no explicit action)"), "",
        f"**ANALYSIS REQUIRED:** {item['analysis_required']}",
    ]
    if item["analysis_required"] != "no" and item["analysis_detail"]:
        lines += ["", f"- Exact analysis: {item['analysis_detail']}",
                  f"- Purpose: address {item['title'].lower()}",
                  "- Resolving result: a result that speaks directly to the claim above; "
                  "if it does not, the concern stands."]
    lines += ["", f"**TEXTUAL REVISION REQUIRED:** {item['textual_revision_required']}"]
    if item["textual_revision_required"] == "yes" and item["textual_detail"]:
        lines += ["", f"- Change: {item['textual_detail']}",
                  f"- Where: {item['manuscript_location'] or 'see issue'}"]
    lines += ["", "**REVIEWER POSITIONS:**", ""]
    lines += [f"- {name}: {position}" for name, position in item["reviewer_positions"].items()]
    lines += ["", f"**AUTHOR POSITION:** {item['author_position']}", "",
              f"**EDITOR DECISION:** {item['editor_decision'] or '(none recorded)'}", "",
              f"**INFLUENCE PATH:** {item['influence_path']}", "",
              f"**CONFIDENCE:** {item['confidence']}", "", "---", ""]
    return lines


# ------------------------------------------------------------- 06 unresolved
def render_unresolved(store: Store, run_id: str) -> str:
    lines = ["# Unresolved disagreements", "",
             "These are genuine disagreements, not failures of the process. The deliberation "
             "did not force consensus.", ""]
    unresolved = [row for row in store.list_issues(run_id)
                  if row["status"] == STATUS_UNRESOLVED or not is_terminal(row["status"])]
    if not unresolved:
        lines.append("None: every issue reached a settled status.")
        return "\n".join(lines)
    for issue in unresolved:
        positions = latest_positions(store, run_id, issue["issue_id"])
        lines += [f"## {issue['issue_id']} - {issue['title']}", "",
                  f"**Status:** {issue['status']}  ",
                  f"**Category:** {issue['category'] or 'n/a'}  ",
                  f"**Severity:** {issue['severity'] or 'n/a'}", "",
                  "**State of play:**", "", (issue["current_summary"] or "(none)").strip(), ""]
        if positions:
            lines += ["**Positions:**", ""]
            lines += [f"- {agent}: {position}" for agent, position in sorted(positions.items())]
            lines += [""]
        if issue["editor_decision"]:
            lines += ["**Editor note:**", "", issue["editor_decision"].strip(), ""]
        remaining = [row for row in store.positions(run_id, issue["issue_id"])
                     if (row["remaining_concern"] or "").strip()
                     and row["remaining_concern"].strip().lower() not in {"none", "n/a"}]
        if remaining:
            lines += ["**Residual concerns:**", ""]
            lines += [f"- {row['agent']}: {row['remaining_concern'].strip()}" for row in remaining]
            lines += [""]
    return "\n".join(lines)


# -------------------------------------------------------------- 07 influence
def render_influence(store: Store, run_id: str) -> str:
    report = influence_report(store, run_id)
    lines = ["# Influence report", "",
             "Who changed whose mind, reconstructed from position changes and the messages "
             "that triggered them.", "",
             f"Total position changes: {report['total_position_changes']}", ""]
    if report["persuasion_counts"]:
        lines += ["## Persuasion counts (arguments that moved someone)", ""]
        lines += [f"- {agent}: {count}" for agent, count in report["persuasion_counts"].items()]
        lines += [""]
    if report["update_counts"]:
        lines += ["## Update counts (times each participant changed position)", ""]
        lines += [f"- {agent}: {count}" for agent, count in report["update_counts"].items()]
        lines += [""]
    if report["edge_counts"]:
        lines += ["## Influence edges", ""]
        lines += [f"- {edge}: {count}" for edge, count in report["edge_counts"].items()]
        lines += [""]
    lines += ["## Issue-by-issue", ""]
    for issue in store.list_issues(run_id):
        influence = issue_influence(store, run_id, issue["issue_id"])
        lines += [narrate_issue(store, run_id, influence), ""]
    if report["invalid_edges"]:
        lines += ["## Warning: unresolved influence links", ""]
        lines += [f"- {edge}" for edge in report["invalid_edges"]]
    return "\n".join(lines)


# ------------------------------------------------------------------ 08 JSON
def build_results(store: Store, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    return {
        "run": {
            "run_id": run["run_id"], "project": run["project"], "manuscript": run["manuscript"],
            "manuscript_hash": run["manuscript_hash"], "inputs": jload(run["inputs_json"], []),
            "journal": run["journal"], "started_at": run["started_at"],
            "ended_at": run["ended_at"], "status": run["status"], "phase": run["phase"],
            "rounds": run["current_round"], "max_rounds": run["max_rounds"],
            "termination_reason": run["termination_reason"],
            "peerreview_version": run["peerreview_version"],
            "config": jload(run["config_json"], {}),
            "model_config": jload(run["model_config_json"], {}),
        },
        "agents": [{"id": row["id"], "role": row["role"], "expertise": row["expertise"],
                    "provider": row["provider"], "model": row["model"],
                    "params": jload(row["params_json"], {}), "status": row["status"]}
                   for row in store.get_agents(run_id)],
        "issues": [{key: row[key] for key in row.keys()} | {"raised_by": _agents_of(row)}
                   for row in store.list_issues(run_id, provisional=None, include_merged=True)],
        "positions": [{key: row[key] for key in row.keys()} for row in store.positions(run_id)],
        "messages": [{key: row[key] for key in row.keys()}
                     | {"recipients": jload(row["recipients"], []),
                        "issue_ids": jload(row["issue_ids"], [])}
                     for row in store.all_messages(run_id)],
        "issue_events": [{key: row[key] for key in row.keys()} for row in store.issue_events(run_id)],
        "interventions": [{key: row[key] for key in row.keys()}
                          for row in store.all_interventions(run_id)],
        "revision_plan": build_revision_plan(store, run_id),
        "influence": influence_report(store, run_id),
        "usage": _usage_summary(store, run_id),
    }


def _usage_summary(store: Store, run_id: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"calls": 0, "by_agent": {}, "errors": 0}
    for row in store.calls(run_id):
        summary["calls"] += 1
        if row["error"]:
            summary["errors"] += 1
        usage = jload(row["usage_json"], {}) or {}
        agent = summary["by_agent"].setdefault(
            row["agent_id"], {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                              "provider": row["provider"], "model": row["model"]})
        agent["calls"] += 1
        agent["input_tokens"] += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        agent["output_tokens"] += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return summary


# ---------------------------------------------------------------- entrypoint
def export_all(store: Store, run_id: str, out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    documents = {
        "initial_reviews": render_initial_reviews(store, run_id),
        "ledger_initial": render_ledger(store, run_id, initial=True),
        "transcript": render_transcript(store, run_id),
        "ledger_final": render_ledger(store, run_id),
        "revision_plan": render_revision_plan(store, run_id),
        "unresolved": render_unresolved(store, run_id),
        "influence": render_influence(store, run_id),
    }
    for key, content in documents.items():
        path = out_dir / FILES[key]
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        written[key] = path
    json_path = out_dir / FILES["json"]
    json_path.write_text(json.dumps(build_results(store, run_id), indent=2, default=str),
                         encoding="utf-8")
    written["json"] = json_path
    # Imported here: dashboard.py reads this module, so a top-level import would cycle.
    from .dashboard import write_dashboard

    written["dashboard"] = write_dashboard(store, run_id, out_dir / FILES["dashboard"])
    return written
