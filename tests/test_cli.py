"""CLI surface: the commands a user actually types."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerreview.cli import main
from peerreview.config import Config

from conftest import DEMO, make_project


def test_init_scaffolds_a_project(tmp_path, capsys):
    target = tmp_path / "paper-project"
    assert main(["init", "my-paper", "--directory", str(target)]) == 0
    assert (target / "peerreview.yaml").exists()
    assert (target / "papers").is_dir()
    config = Config.load(target)
    assert config.name == "my-paper"
    assert config.deliberation["max_rounds"] == 20
    with pytest.raises(SystemExit):
        main(["init", "my-paper", "--directory", str(target)])


def test_dry_run_makes_no_model_calls(tmp_path, capsys):
    config = make_project(tmp_path)
    assert main(["run", str(config.root), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "No model calls were made" in out
    assert "Reviewer2" in out and "mock:mock-1" in out
    assert not config.db_path.exists()


def test_run_status_issues_transcript_export(tmp_path, capsys):
    config = make_project(tmp_path)
    project = str(config.root)
    assert main(["run", project, "--quiet"]) == 0
    capsys.readouterr()

    assert main(["status", project]) == 0
    status = capsys.readouterr().out
    assert "Status:       finished" in status
    assert "Issues:" in status and "Positions:" in status

    assert main(["issues", project]) == 0
    issues = capsys.readouterr().out
    assert "ISSUE-001" in issues and "UNRESOLVED DISAGREEMENT" in issues

    assert main(["issues", project, "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed and parsed[0]["issue_id"] == "ISSUE-001"

    assert main(["transcript", project, "--issue", "ISSUE-002"]) == 0
    transcript = capsys.readouterr().out
    assert "ISSUE-002" in transcript and "Reviewer2" in transcript

    out_dir = tmp_path / "exported"
    assert main(["export", project, "--out", str(out_dir)]) == 0
    capsys.readouterr()
    assert (out_dir / "05_revision_plan.md").exists()
    assert (out_dir / "08_machine_readable_results.json").exists()


def test_intervene_queues_input_for_the_next_round(tmp_path, capsys):
    config = make_project(tmp_path)
    project = str(config.root)
    assert main(["run", project, "--quiet"]) == 0
    capsys.readouterr()

    assert main(["intervene", project, "--message", "Focus on identification.",
                 "--as", "Author", "--issue", "ISSUE-001"]) == 0
    assert "queued human message" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="requires --reason"):
        main(["intervene", project, "--reopen", "ISSUE-001"])
    with pytest.raises(SystemExit, match="nothing to do"):
        main(["intervene", project])

    from peerreview.db import Store
    store = Store(config.db_path)
    run = store.latest_run(config.name)
    pending = store.pending_interventions(run["run_id"])
    assert len(pending) == 1 and pending[0]["speak_as"] == "Author"


def test_status_all_lists_runs(tmp_path, capsys):
    config = make_project(tmp_path)
    assert main(["run", str(config.root), "--quiet"]) == 0
    capsys.readouterr()
    assert main(["status", str(config.root), "--all"]) == 0
    assert "austerity-demo" in capsys.readouterr().out


def test_demo_command_runs_end_to_end(tmp_path, capsys):
    target = tmp_path / "demo"
    assert main(["demo", "--directory", str(target)]) == 0
    out = capsys.readouterr().out
    assert "Phase 1: independent reviews" in out
    assert "changes position" in out
    runs = list((target / "runs").glob("*/05_revision_plan.md"))
    assert runs, "demo produced no revision plan"
    assert "ISSUE-002" in runs[0].read_text(encoding="utf-8")
