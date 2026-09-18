"""Optional final stage: the Author produces a revised manuscript and a response letter.

Enabled with `revision_stage.enabled: true`. Only revisions in the plan may be
made; the change log records what was actually touched and flags anything in
the plan that the revised text does not reference.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import prompts
from .context import Part
from .exports import FILES, build_revision_plan, render_revision_plan
from .models import AUTHOR, DISMISSED_STATUSES
from .orchestrator import PHASE_REVISION, Orchestrator

# `[^<]` keeps a marker from swallowing the text up to the *next* marker.
REV_MARKER_RE = re.compile(r"<!--\s*rev:\s*(ISSUE-\d{3})[^<]*?-->", re.I)


def _plan_part(store, run_id: str) -> Part:
    return Part("REVISION PLAN (implement exactly this, nothing more)",
                render_revision_plan(store, run_id), priority=1, floor_tokens=1500)


def run_revision_stage(orchestrator: Orchestrator, out_dir: Path) -> dict[str, Path]:
    store, run_id = orchestrator.store, orchestrator.run_id
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    round_number = int(orchestrator.run_row["current_round"] or 0) + 1
    orchestrator._set_phase(PHASE_REVISION, round_number)
    orchestrator.reporter.phase("Optional stage: manuscript revision")

    plan = build_revision_plan(store, run_id)
    actionable = [item for item in plan if not item["dismissed"]]

    manuscript_part = Part("FULL MANUSCRIPT", orchestrator.bundle.full_text(kinds=["manuscript"]),
                           priority=1, floor_tokens=4000)
    request = orchestrator._request(
        AUTHOR, phase=PHASE_REVISION, round=round_number, directive=prompts.REVISION_STAGE,
        focus_issue_ids=[item["issue_id"] for item in actionable][:12],
        extra_parts=[manuscript_part, _plan_part(store, run_id)])
    result = orchestrator.pool.run_one(request)
    orchestrator._speak(result, phase=PHASE_REVISION, round=round_number,
                        message_type="revision", recipients=["ALL"])
    revised = (result.text or "").strip()

    letter_request = orchestrator._request(
        AUTHOR, phase=PHASE_REVISION, round=round_number,
        directive=prompts.RESPONSE_LETTER_STAGE,
        focus_issue_ids=[item["issue_id"] for item in plan][:14],
        extra_parts=[_plan_part(store, run_id),
                     Part("THE REVISED MANUSCRIPT YOU JUST PRODUCED", revised, priority=2,
                          floor_tokens=1000)])
    letter_result = orchestrator.pool.run_one(letter_request)
    orchestrator._speak(letter_result, phase=PHASE_REVISION, round=round_number,
                        message_type="revision", recipients=["ALL"])
    letter = (letter_result.text or "").strip()

    written: dict[str, Path] = {}
    manuscript_path = out_dir / FILES["revised_manuscript"]
    manuscript_path.write_text(revised + "\n\n" + _change_log(revised, plan), encoding="utf-8")
    written["revised_manuscript"] = manuscript_path
    letter_path = out_dir / FILES["response_letter"]
    letter_path.write_text(letter + "\n", encoding="utf-8")
    written["response_letter"] = letter_path
    orchestrator.store.update_run(run_id, phase="done")
    return written


def _change_log(revised: str, plan: list[dict[str, Any]]) -> str:
    referenced = {match.group(1).upper() for match in REV_MARKER_RE.finditer(revised)}
    by_id = {item["issue_id"]: item for item in plan}
    lines = ["", "---", "", "## Change log", "",
             "Each entry links a manuscript change to the issue that required it.", ""]
    for issue_id in sorted(referenced):
        item = by_id.get(issue_id)
        if item is None:
            lines.append(f"- **{issue_id}**: referenced in the revised text but NOT in the "
                         f"revision plan - review this change before accepting it.")
        else:
            lines.append(f"- **{issue_id}** ({item['classification']}): {item['title']} - "
                         f"{item['required_action'] or 'see revision plan'}")
    missing = [item for item in plan
               if not item["dismissed"] and item["issue_id"] not in referenced
               and item["status"] not in DISMISSED_STATUSES]
    if missing:
        lines += ["", "### Plan items not marked in the revised text", ""]
        lines += [f"- **{item['issue_id']}**: {item['title']} ({item['classification']}) - "
                  f"verify whether this was implemented" for item in missing]
    return "\n".join(lines) + "\n"
