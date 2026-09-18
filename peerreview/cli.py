"""Command-line interface.

    peerreview init <name>          scaffold a project
    peerreview run <project>        independent reviews -> consolidation -> deliberation
    peerreview status <project>     where a run stands
    peerreview issues <project>     the ledger
    peerreview transcript <project> the shared transcript
    peerreview resume <project>     continue an interrupted run
    peerreview export <project>     (re)write the run outputs
    peerreview intervene <project>  insert a human message, reopen an issue, pause/resume
    peerreview demo                 end-to-end run on the bundled example, using mock models
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import Config, render_template
from .db import Store
from .exports import export_all, run_directory
from .models import is_terminal
from .orchestrator import Orchestrator
from .reporting import Reporter
from .util import jload

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


# --------------------------------------------------------------------- helpers
def load_config(target: str | None) -> Config:
    return Config.load(Path(target or "."))


def open_store(config: Config) -> Store:
    return Store(config.db_path)


def pick_run(store: Store, config: Config, run_id: str | None) -> sqlite3.Row:
    run = store.get_run(run_id) if run_id else store.latest_run(config.name)
    if run is None:
        raise SystemExit(f"no run found for project '{config.name}' in {config.db_path}")
    return run


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    data = copy.deepcopy(config.data)
    if getattr(args, "max_rounds", None):
        data["deliberation"]["max_rounds"] = args.max_rounds
    if getattr(args, "isolation", None):
        data["execution"]["isolation"] = args.isolation
    if getattr(args, "provider", None):
        data["defaults"]["provider"] = args.provider
    if getattr(args, "model", None):
        data["defaults"]["model"] = args.model
    if getattr(args, "mock_script", None):
        script = json.loads(Path(args.mock_script).read_text(encoding="utf-8"))
        data["defaults"].update({"provider": "mock", "model": "mock-1", "script": script})
        for key in data.get("agents", {}):
            data["agents"][key].pop("provider", None)
            data["agents"][key].pop("model", None)
    if getattr(args, "revision_stage", False):
        data.setdefault("revision_stage", {})["enabled"] = True
    return Config(data=data, path=config.path)


# ---------------------------------------------------------------------- init
def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.directory or args.name)
    (root / "papers").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    config_path = root / "peerreview.yaml"
    if config_path.exists() and not args.force:
        raise SystemExit(f"{config_path} already exists (use --force to overwrite)")
    config_path.write_text(render_template(args.name, args.provider, args.model), encoding="utf-8")
    manuscript = root / "papers" / "manuscript.md"
    if not manuscript.exists():
        manuscript.write_text("# Title\n\n## Abstract\n\nReplace this file with your "
                              "manuscript (.md or .pdf) and update peerreview.yaml.\n",
                              encoding="utf-8")
    print(f"Initialized {root}/")
    print(f"  {config_path}")
    print(f"  {manuscript}")
    print("\nNext: put your manuscript in papers/, set the paths in peerreview.yaml,")
    print(f"export your provider key, then run:  peerreview run {root}")
    return 0


# ----------------------------------------------------------------------- run
def _run(config: Config, args: argparse.Namespace, *, resume_run_id: str | None = None) -> int:
    reporter = Reporter(verbose=args.verbose, quiet=args.quiet)
    store = open_store(config)
    if resume_run_id:
        orchestrator = Orchestrator.resume(config, resume_run_id, store=store, reporter=reporter)
        print(f"Resuming run {resume_run_id}")
    else:
        orchestrator = Orchestrator.create(config, store=store, reporter=reporter)
        print(f"Run {orchestrator.run_id}")
        print(f"Manuscript: {orchestrator.bundle.documents[0].path} "
              f"({len(orchestrator.bundle.sections)} sections, "
              f"sha256 {orchestrator.bundle.documents[0].sha256[:12]})")
    orchestrator.run()
    out_dir = run_directory(config.runs_dir, store.get_run(orchestrator.run_id))
    written = export_all(store, orchestrator.run_id, out_dir)
    if config.revision_stage_enabled:
        from .revision import run_revision_stage
        written.update(run_revision_stage(orchestrator, out_dir))
    print(f"\nOutputs in {out_dir}/")
    for path in written.values():
        print(f"  {path.name}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = apply_overrides(load_config(args.project), args)
    if args.dry_run:
        return _dry_run(config)
    return _run(config, args)


def cmd_resume(args: argparse.Namespace) -> int:
    config = apply_overrides(load_config(args.project), args)
    store = open_store(config)
    run = pick_run(store, config, args.run_id)
    if run["status"] == "finished" and not args.force:
        print(f"run {run['run_id']} already finished ({run['termination_reason']}); "
              f"use --force to continue anyway")
        return 0
    return _run(config, args, resume_run_id=run["run_id"])


def _dry_run(config: Config) -> int:
    from .manuscript import load_bundle

    inputs = config.manuscript_inputs()
    bundle = load_bundle(manuscript=inputs["manuscript"], appendix=inputs["appendix"],
                         supplementary=inputs["supplementary"], codebook=inputs["codebook"],
                         journal=config.journal)
    print(f"Project: {config.name}")
    print(f"Journal: {config.journal or 'unspecified'}")
    print(f"Database: {config.db_path}")
    print(f"Max rounds: {config.deliberation['max_rounds']}  "
          f"isolation: {config.execution['isolation']}")
    print("\nManuscript:")
    for document in bundle.documents:
        print(f"  {document.kind}: {document.path} "
              f"({len(document.sections)} sections, sha256 {document.sha256[:12]})")
    print("\nOutline:")
    print("\n".join("  " + line for line in bundle.outline().splitlines()[:40]))
    print("\nAgents:")
    for spec in config.agent_specs().values():
        print(f"  {spec.id:<10} {spec.role:<8} {spec.provider}:{spec.model} "
              f"(system prompt {len(spec.system_prompt)} chars)")
    print("\nNo model calls were made (--dry-run).")
    return 0


# -------------------------------------------------------------------- status
def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.project)
    store = open_store(config)
    if args.all:
        for run in store.list_runs():
            print(f"{run['run_id']}  {run['status']:<9} phase={run['phase']:<16} "
                  f"round={run['current_round']}  {run['project']}")
        return 0
    run = pick_run(store, config, args.run_id)
    issues = store.list_issues(run["run_id"])
    open_issues = [row for row in issues if not is_terminal(row["status"])]
    positions = store.positions(run["run_id"])
    changes = [row for row in positions if row["changed_from"] and row["changed_from"] != row["position"]]
    print(f"Run:          {run['run_id']}")
    print(f"Project:      {run['project']}")
    print(f"Manuscript:   {run['manuscript']} (sha256 {(run['manuscript_hash'] or '')[:12]})")
    print(f"Status:       {run['status']}   phase: {run['phase']}   round: {run['current_round']}"
          f"/{run['max_rounds']}")
    print(f"Started:      {run['started_at']}")
    print(f"Ended:        {run['ended_at'] or '-'}")
    print(f"Termination:  {run['termination_reason'] or '-'}")
    print(f"Issues:       {len(issues)} ({len(open_issues)} open)")
    print(f"Messages:     {len(store.all_messages(run['run_id']))}")
    print(f"Positions:    {len(positions)} recorded, {len(changes)} changes")
    print(f"Model calls:  {len(store.calls(run['run_id']))}")
    interventions = store.all_interventions(run["run_id"])
    if interventions:
        pending = len([row for row in interventions if not row["consumed"]])
        print(f"Human input:  {len(interventions)} ({pending} pending)")
    by_status: dict[str, int] = {}
    for row in issues:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    if by_status:
        print("\nStatus breakdown:")
        for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>3}  {status}")
    return 0


# -------------------------------------------------------------------- issues
def cmd_issues(args: argparse.Namespace) -> int:
    config = load_config(args.project)
    store = open_store(config)
    run = pick_run(store, config, args.run_id)
    rows = store.list_issues(run["run_id"], provisional=None if args.provisional else False,
                             include_merged=args.provisional)
    if args.open:
        rows = [row for row in rows if not is_terminal(row["status"])]
    if args.status:
        rows = [row for row in rows if args.status.upper() in row["status"].upper()]
    if args.json:
        print(json.dumps([{key: row[key] for key in row.keys()} for row in rows], indent=2))
        return 0
    for row in rows:
        raised = ", ".join(jload(row["raised_by"], []) or [])
        print(f"{row['issue_id']}  [{row['status']}]  {row['severity'] or '-'}/"
              f"{row['category'] or '-'}  {row['title']}")
        if raised:
            print(f"    raised by: {raised}")
        if row["required_action"]:
            print(f"    action: {row['required_action'][:160]}")
        if args.verbose and row["current_summary"]:
            print(f"    summary: {row['current_summary'][:400]}")
    if not rows:
        print("(no issues)")
    return 0


# ---------------------------------------------------------------- transcript
def cmd_transcript(args: argparse.Namespace) -> int:
    config = load_config(args.project)
    store = open_store(config)
    run = pick_run(store, config, args.run_id)
    for row in store.all_messages(run["run_id"]):
        if args.round is not None and row["round"] != args.round:
            continue
        issue_ids = jload(row["issue_ids"], []) or []
        if args.issue and args.issue.upper() not in [i.upper() for i in issue_ids]:
            continue
        if args.agent and row["sender"] != args.agent:
            continue
        recipients = ", ".join(jload(row["recipients"], []) or [])
        marker = " [private]" if row["visibility"] == "private" and row["disclosed_at_round"] is None else ""
        human = " [HUMAN]" if row["is_human"] else ""
        print(f"\n[M{row['id']}] round {row['round']} {row['sender']} -> {recipients}"
              f"{(' (' + ', '.join(issue_ids) + ')') if issue_ids else ''}{marker}{human}")
        body = row["content"].strip()
        print(body if args.full else (body[:1200] + ("..." if len(body) > 1200 else "")))
    return 0


# -------------------------------------------------------------------- export
def cmd_export(args: argparse.Namespace) -> int:
    config = load_config(args.project)
    store = open_store(config)
    run = pick_run(store, config, args.run_id)
    out_dir = Path(args.out) if args.out else run_directory(config.runs_dir, run)
    written = export_all(store, run["run_id"], out_dir)
    print(f"Wrote {len(written)} files to {out_dir}/")
    for path in written.values():
        print(f"  {path.name}")
    return 0


# ----------------------------------------------------------------- dashboard
def cmd_dashboard(args: argparse.Namespace) -> int:
    from .dashboard import write_dashboard

    config = load_config(args.project)
    store = open_store(config)
    run = pick_run(store, config, args.run_id)
    out = (Path(args.out) if args.out
           else run_directory(config.runs_dir, run) / "11_dashboard.html")
    path = write_dashboard(store, run["run_id"], out)
    size = path.stat().st_size
    print(f"Wrote {path} ({size // 1024} KB, self-contained)")
    if args.open:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())
    else:
        print(f"Open it with:  open {path}")
    return 0


# ----------------------------------------------------------------- intervene
def cmd_intervene(args: argparse.Namespace) -> int:
    config = load_config(args.project)
    store = open_store(config)
    run = pick_run(store, config, args.run_id)
    run_id = run["run_id"]
    round_number = int(run["current_round"] or 0)
    actions = 0
    if args.pause:
        store.add_intervention(run_id, round=round_number, kind="pause")
        store.update_run(run_id, status="paused")
        print("run will pause at the next round boundary")
        actions += 1
    if args.unpause:
        store.add_intervention(run_id, round=round_number, kind="resume")
        store.update_run(run_id, status="running")
        print("run unpaused")
        actions += 1
    text = args.message
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if text:
        kind = "evidence" if args.evidence else ("correction" if args.correction else "message")
        store.add_intervention(run_id, round=round_number, kind=kind, speak_as=args.speak_as,
                               issue_id=args.issue, payload={"text": text})
        print(f"queued human {kind} as {args.speak_as or 'HUMAN'}"
              + (f" on {args.issue}" if args.issue else ""))
        actions += 1
    if args.reopen:
        if not args.reason:
            raise SystemExit("--reopen requires --reason (closed issues never reopen silently)")
        store.add_intervention(run_id, round=round_number, kind="reopen", issue_id=args.reopen,
                               payload={"reason": args.reason})
        print(f"queued reopen of {args.reopen}")
        actions += 1
    if args.focus:
        ids = [i.strip() for i in args.focus.split(",") if i.strip()]
        store.add_intervention(run_id, round=round_number, kind="focus",
                               payload={"issue_ids": ids})
        print(f"queued editor focus on {', '.join(ids)}")
        actions += 1
    if not actions:
        raise SystemExit("nothing to do: pass --message/--file, --reopen, --focus, --pause "
                         "or --unpause")
    print("interventions are applied at the start of the next round "
          f"(resume with: peerreview resume {args.project or '.'})")
    return 0


# ---------------------------------------------------------------------- demo
def cmd_demo(args: argparse.Namespace) -> int:
    source = EXAMPLES / "demo"
    if not source.exists():
        raise SystemExit(f"bundled example not found at {source}")
    target = Path(args.directory)
    if target.exists() and any(target.iterdir()) and not args.force:
        raise SystemExit(f"{target} is not empty (use --force)")
    if target.exists() and args.force:
        shutil.rmtree(target)
    shutil.copytree(source, target)
    print(f"Copied the bundled example to {target}/ (mock models: no API calls, no cost)\n")
    config = load_config(str(target))
    namespace = argparse.Namespace(verbose=args.verbose, quiet=False, dry_run=False,
                                   max_rounds=args.max_rounds, isolation=None, provider=None,
                                   model=None, mock_script=None, revision_stage=args.revision_stage,
                                   project=str(target))
    config = apply_overrides(config, namespace)
    return _run(config, namespace)


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="peerreview",
                                     description="Multi-agent peer-review deliberation")
    parser.add_argument("--version", action="version", version=f"peerreview {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("project", nargs="?", default=".",
                        help="project directory or config file (default: .)")
    common.add_argument("--run-id", help="operate on a specific run instead of the latest")

    p_init = sub.add_parser("init", help="scaffold a new project")
    p_init.add_argument("name")
    p_init.add_argument("--directory", help="target directory (default: ./<name>)")
    p_init.add_argument("--provider", default="anthropic")
    p_init.add_argument("--model", default="claude-sonnet-5")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", parents=[common], help="run a full deliberation")
    p_run.add_argument("-v", "--verbose", action="store_true", help="print every message")
    p_run.add_argument("-q", "--quiet", action="store_true")
    p_run.add_argument("--max-rounds", type=int)
    p_run.add_argument("--isolation", choices=["serial", "thread", "process"])
    p_run.add_argument("--provider", help="override the default provider for all agents")
    p_run.add_argument("--model", help="override the default model for all agents")
    p_run.add_argument("--mock-script", help="run with deterministic mock models from a JSON script")
    p_run.add_argument("--revision-stage", action="store_true",
                       help="also produce 09_revised_manuscript.md and 10_response_to_reviewers.md")
    p_run.add_argument("--dry-run", action="store_true",
                       help="resolve config and manuscript, make no model calls")
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume", parents=[common], help="continue an interrupted run")
    p_resume.add_argument("-v", "--verbose", action="store_true")
    p_resume.add_argument("-q", "--quiet", action="store_true")
    p_resume.add_argument("--max-rounds", type=int)
    p_resume.add_argument("--isolation", choices=["serial", "thread", "process"])
    p_resume.add_argument("--provider")
    p_resume.add_argument("--model")
    p_resume.add_argument("--mock-script")
    p_resume.add_argument("--revision-stage", action="store_true")
    p_resume.add_argument("--force", action="store_true")
    p_resume.set_defaults(func=cmd_resume, dry_run=False)

    p_status = sub.add_parser("status", parents=[common], help="show where a run stands")
    p_status.add_argument("--all", action="store_true", help="list every run in the database")
    p_status.set_defaults(func=cmd_status)

    p_issues = sub.add_parser("issues", parents=[common], help="show the issue ledger")
    p_issues.add_argument("--open", action="store_true", help="only issues not yet terminal")
    p_issues.add_argument("--status", help="filter by status substring")
    p_issues.add_argument("--provisional", action="store_true",
                          help="include the pre-consolidation reviewer issues")
    p_issues.add_argument("--json", action="store_true")
    p_issues.add_argument("-v", "--verbose", action="store_true")
    p_issues.set_defaults(func=cmd_issues)

    p_transcript = sub.add_parser("transcript", parents=[common], help="print the transcript")
    p_transcript.add_argument("--round", type=int)
    p_transcript.add_argument("--issue")
    p_transcript.add_argument("--agent")
    p_transcript.add_argument("--full", action="store_true", help="do not truncate messages")
    p_transcript.set_defaults(func=cmd_transcript)

    p_export = sub.add_parser("export", parents=[common], help="write the run outputs")
    p_export.add_argument("--out", help="output directory (default: runs/<date>-<project>)")
    p_export.set_defaults(func=cmd_export)

    p_dashboard = sub.add_parser("dashboard", parents=[common],
                                 help="write a self-contained HTML dashboard for a run")
    p_dashboard.add_argument("--out", help="output path (default: the run directory)")
    p_dashboard.add_argument("--open", action="store_true", help="open it in a browser")
    p_dashboard.set_defaults(func=cmd_dashboard)

    p_intervene = sub.add_parser("intervene", parents=[common],
                                 help="insert human input, reopen an issue, pause or resume")
    p_intervene.add_argument("--message", help="text to insert into the deliberation")
    p_intervene.add_argument("--file", help="read the message from a file")
    p_intervene.add_argument("--as", dest="speak_as", default="Author",
                             help="identity to speak as (default: Author)")
    p_intervene.add_argument("--issue", help="attach the message to an issue id")
    p_intervene.add_argument("--correction", action="store_true",
                             help="mark this as a correction of an agent")
    p_intervene.add_argument("--evidence", action="store_true",
                             help="mark this as new evidence")
    p_intervene.add_argument("--reopen", help="issue id to reopen (requires --reason)")
    p_intervene.add_argument("--reason", help="reason for reopening")
    p_intervene.add_argument("--focus", help="comma-separated issue ids the Editor must prioritise")
    p_intervene.add_argument("--pause", action="store_true")
    p_intervene.add_argument("--unpause", action="store_true")
    p_intervene.set_defaults(func=cmd_intervene)

    p_demo = sub.add_parser("demo", help="run the bundled example with deterministic mock models")
    p_demo.add_argument("--directory", default="demo-run")
    p_demo.add_argument("-v", "--verbose", action="store_true")
    p_demo.add_argument("--max-rounds", type=int, default=6)
    p_demo.add_argument("--revision-stage", action="store_true")
    p_demo.add_argument("--force", action="store_true")
    p_demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # `peerreview transcript ... | head` closes the pipe; exit quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        if "--traceback" in (argv or sys.argv):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
