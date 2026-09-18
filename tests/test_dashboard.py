"""The HTML dashboard: self-contained, safe to open, and faithful to the run."""
from __future__ import annotations

import json
import re

from peerreview.dashboard import build_payload, render_dashboard, write_dashboard
from peerreview.exports import export_all


def _payload_from_html(html: str) -> dict:
    match = re.search(r'<script id="payload" type="application/json">(.*?)</script>',
                      html, re.S)
    assert match, "payload script block not found"
    return json.loads(match.group(1))


def test_dashboard_is_self_contained(demo_run):
    store, run_id, _ = demo_run
    html = render_dashboard(store, run_id)
    assert html.startswith("<!doctype html>")
    # No network at view time: no external scripts, styles, images or fonts.
    for pattern in (r'src="https?://', r'href="https?://', r"@import", r"url\(https?://"):
        assert not re.search(pattern, html), f"external reference: {pattern}"
    assert "<script src" not in html
    assert html.count("<style>") == 1


def test_payload_carries_the_run(demo_run):
    store, run_id, _ = demo_run
    payload = _payload_from_html(render_dashboard(store, run_id))
    assert payload["run"]["run_id"] == run_id
    assert len(payload["agents"]) == 5
    assert payload["issues"] and payload["messages"] and payload["positions"]
    assert payload["stats"]["issues"] == len(store.list_issues(run_id))
    assert payload["stats"]["changes"] == len(
        [p for p in payload["positions"] if p["changed"]])
    # Every issue carries what the ledger says about it.
    for issue in payload["issues"]:
        assert issue["status"] and issue["tone"] in {
            "good", "warning", "serious", "critical", "neutral"}
        assert "path" in issue and "positions" in issue


def test_action_counts_match_the_ledger(demo_run):
    store, run_id, _ = demo_run
    payload = build_payload(store, run_id)
    assert sum(item["count"] for item in payload["action_counts"]) == len(payload["issues"])
    labels = {item["label"] for item in payload["action_counts"]}
    assert "Unresolved disagreement" in labels
    assert "New analysis" in labels


def test_influence_edges_are_rendered(demo_run):
    store, run_id, _ = demo_run
    payload = build_payload(store, run_id)
    assert payload["influence"]["edges"], "the scripted run has persuasion edges"
    assert "Reviewer2 -> Reviewer3" in payload["influence"]["edges"]
    assert payload["influence"]["invalid"] == []


def test_content_cannot_break_out_of_the_script_block(tmp_path, demo_run):
    """Manuscript and agent text is attacker-ish data, not markup."""
    store, run_id, _ = demo_run
    hostile = '</script><script>window.pwned=1</script> & <img src=x onerror=alert(1)>'
    store.add_message(run_id=run_id, round=99, phase="deliberation", sender="Author",
                      recipients=["ALL"], content=hostile, message_type="deliberation")
    html = render_dashboard(store, run_id)
    # The literal closing tag never appears inside the payload block.
    payload_block = re.search(r'<script id="payload".*?</script>', html, re.S).group(0)
    assert "window.pwned" in payload_block          # the text is preserved...
    assert "</script><script>" not in payload_block  # ...but cannot terminate the block
    payload = _payload_from_html(html)
    assert any(hostile == m["content"] for m in payload["messages"]), "content survived intact"


def test_export_all_writes_the_dashboard(demo_run, tmp_path):
    store, run_id, _ = demo_run
    written = export_all(store, run_id, tmp_path / "out")
    path = written["dashboard"]
    assert path.name == "11_dashboard.html"
    assert path.stat().st_size > 10_000


def test_cli_dashboard_command(tmp_path, capsys):
    from peerreview.cli import main
    from conftest import make_project

    config = make_project(tmp_path)
    project = str(config.root)
    assert main(["run", project, "--quiet"]) == 0
    capsys.readouterr()
    out_path = tmp_path / "dash.html"
    assert main(["dashboard", project, "--out", str(out_path)]) == 0
    assert "self-contained" in capsys.readouterr().out
    assert out_path.exists() and "deliberation dashboard" in out_path.read_text()


def test_write_dashboard_creates_parents(demo_run, tmp_path):
    store, run_id, _ = demo_run
    path = write_dashboard(store, run_id, tmp_path / "a" / "b" / "d.html")
    assert path.exists()
