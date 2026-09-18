"""Self-contained HTML dashboard for a run.

Everything is inlined -- no CDN, no build step, no network at view time -- so the
file can be opened offline, mailed to a co-author, or committed beside the run.

Colour decisions worth knowing:

* Agents are identified by label, never by hue. Five categorical colours cannot
  clear colour-vision-deficiency separation on an all-pairs surface like a node
  graph, so identity rides on text and the marks stay neutral.
* Issue statuses use a reserved status palette and always ship with their text,
  so status is never carried by colour alone.
* Charts are single-hue with direct value labels: one series needs no legend.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Store
from .exports import build_revision_plan, classification, issue_priority, latest_positions
from .influence import influence_report, issue_influence
from .models import (AUTHOR, DISMISSED_STATUSES, EDITOR, REVIEWERS, STATUS_CLARIFICATION,
                     STATUS_EXPOSITION, STATUS_NEW_ANALYSIS, STATUS_OPEN,
                     STATUS_ROBUSTNESS_OPTIONAL, STATUS_SUBSTANTIVE, STATUS_UNRESOLVED,
                     is_terminal)
from .util import jload

# What the author actually has to do, which is the question the dashboard answers.
ACTION_GROUPS = [
    ("New analysis", [STATUS_NEW_ANALYSIS]),
    ("Substantive revision", [STATUS_SUBSTANTIVE]),
    ("Clarification / exposition", [STATUS_CLARIFICATION, STATUS_EXPOSITION]),
    ("Optional robustness", [STATUS_ROBUSTNESS_OPTIONAL]),
    ("No change required", sorted(DISMISSED_STATUSES) + ["RESOLVED", "CLOSED"]),
    ("Unresolved disagreement", [STATUS_UNRESOLVED]),
    ("Still open", [STATUS_OPEN]),
]

STATUS_TONE = {
    STATUS_NEW_ANALYSIS: "serious",
    STATUS_SUBSTANTIVE: "serious",
    STATUS_CLARIFICATION: "warning",
    STATUS_EXPOSITION: "warning",
    STATUS_ROBUSTNESS_OPTIONAL: "warning",
    STATUS_UNRESOLVED: "neutral",     # a legitimate outcome, not a failure
    STATUS_OPEN: "critical",
}


def _tone(status: str) -> str:
    return STATUS_TONE.get(status, "good")


def build_payload(store: Store, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    issues = store.list_issues(run_id)
    plan = {item["issue_id"]: item for item in build_revision_plan(store, run_id)}
    influence = influence_report(store, run_id)
    paths = {item["issue_id"]: item["influence_path"] for item in influence["issues"]}

    messages = []
    for row in store.all_messages(run_id):
        messages.append({
            "id": row["id"], "round": row["round"], "phase": row["phase"],
            "sender": row["sender"], "recipients": jload(row["recipients"], []) or [],
            "issue_ids": jload(row["issue_ids"], []) or [], "type": row["message_type"],
            "content": row["content"], "human": bool(row["is_human"]),
            "private": row["visibility"] == "private",
            "disclosed": row["disclosed_at_round"],
        })

    positions = []
    for row in store.positions(run_id):
        positions.append({
            "issue_id": row["issue_id"], "round": row["round"], "agent": row["agent"],
            "position": row["position"], "from": row["changed_from"],
            "rationale": row["rationale"] or "", "trigger_id": row["triggered_by_message_id"],
            "trigger_agent": row["triggered_by_agent"],
            "remaining": row["remaining_concern"] or "",
            "changed": bool(row["changed_from"] and row["changed_from"] != row["position"]),
        })

    issue_rows = []
    for row in issues:
        issue_id = row["issue_id"]
        item = plan.get(issue_id, {})
        issue_rows.append({
            "id": issue_id, "title": row["title"], "status": row["status"],
            "tone": _tone(row["status"]), "severity": row["severity"] or "n/a",
            "category": row["category"] or "n/a", "priority": issue_priority(row),
            "raised_by": jload(row["raised_by"], []) or [],
            "location": row["manuscript_location"] or "",
            "description": row["description"] or "", "summary": row["current_summary"] or "",
            "action": row["required_action"] or "", "decision": row["editor_decision"] or "",
            "created_round": row["created_round"], "closed_round": row["closed_round"],
            "classification": classification(row), "terminal": is_terminal(row["status"]),
            "positions": latest_positions(store, run_id, issue_id),
            "path": paths.get(issue_id, ""),
            "analysis": item.get("analysis_required", "no"),
            "analysis_detail": item.get("analysis_detail", ""),
            "textual": item.get("textual_revision_required", "no"),
            "textual_detail": item.get("textual_detail", ""),
            "why": item.get("why_it_matters", ""),
            "original_comment": row["original_comment"] or "",
        })

    counts = []
    for label, statuses in ACTION_GROUPS:
        n = len([row for row in issue_rows if row["status"] in statuses])
        if n:
            counts.append({"label": label, "count": n})

    changes = [p for p in positions if p["changed"]]
    by_round: dict[int, int] = {}
    for change in changes:
        by_round[change["round"]] = by_round.get(change["round"], 0) + 1

    calls = store.calls(run_id)
    tokens_in = tokens_out = 0
    for row in calls:
        usage = jload(row["usage_json"], {}) or {}
        tokens_in += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        tokens_out += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)

    return {
        "run": {
            "run_id": run["run_id"], "project": run["project"], "manuscript": run["manuscript"],
            "manuscript_hash": (run["manuscript_hash"] or "")[:12], "journal": run["journal"],
            "started_at": run["started_at"], "ended_at": run["ended_at"],
            "status": run["status"], "rounds": run["current_round"],
            "max_rounds": run["max_rounds"], "termination": run["termination_reason"] or "",
            "version": run["peerreview_version"],
        },
        "agents": [{"id": row["id"], "role": row["role"], "provider": row["provider"],
                    "model": row["model"], "expertise": row["expertise"] or ""}
                   for row in store.get_agents(run_id)],
        "issues": issue_rows,
        "messages": messages,
        "positions": positions,
        "plan": list(plan.values()),
        "influence": {
            "edges": influence["edge_counts"],
            "persuaded": influence["persuasion_counts"],
            "updated": influence["update_counts"],
            "invalid": influence["invalid_edges"],
        },
        "action_counts": counts,
        "changes_by_round": [{"round": r, "count": c} for r, c in sorted(by_round.items())],
        "stats": {
            "issues": len(issue_rows),
            "open": len([row for row in issue_rows if not row["terminal"]]),
            "changes": len(changes),
            "messages": len(messages),
            "calls": len(calls),
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "unresolved": len([row for row in issue_rows if row["status"] == STATUS_UNRESOLVED]),
            "new_analysis": len([row for row in issue_rows
                                 if row["status"] == STATUS_NEW_ANALYSIS]),
        },
    }


def render_dashboard(store: Store, run_id: str) -> str:
    payload = build_payload(store, run_id)
    data = json.dumps(payload, default=str).replace("</", "<\\/")
    title = f"{payload['run']['project']} — peerreview"
    return TEMPLATE.replace("__TITLE__", _escape(title)).replace("__DATA__", data)


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def write_dashboard(store: Store, run_id: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(store, run_id), encoding="utf-8")
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --plane: #f9f9f7;  --surface: #fcfcfb;  --ink: #0b0b0b;  --ink-2: #52514e;
  --muted: #898781;  --grid: #e1e0d9;  --rule: #c3c2b7;  --ring: rgba(11,11,11,.10);
  --series: #2a78d6;
  --good: #0ca30c;  --warning: #fab219;  --serious: #ec835a;  --critical: #d03b3b;
  --radius: 10px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --plane: #0d0d0d;  --surface: #1a1a19;  --ink: #fff;  --ink-2: #c3c2b7;
    --muted: #898781;  --grid: #2c2c2a;  --rule: #383835; --ring: rgba(255,255,255,.10);
    --series: #3987e5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --plane: #0d0d0d;  --surface: #1a1a19;  --ink: #fff;  --ink-2: #c3c2b7;
  --muted: #898781;  --grid: #2c2c2a;  --rule: #383835; --ring: rgba(255,255,255,.10);
  --series: #3987e5;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--plane); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 24px 16px 64px; }
header { display: flex; flex-wrap: wrap; gap: 16px; align-items: baseline; justify-content: space-between; }
h1 { font-size: 21px; margin: 0; letter-spacing: -.01em; }
h2 { font-size: 15px; margin: 0 0 12px; font-weight: 600; }
.sub { color: var(--ink-2); font-size: 13px; margin-top: 4px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.card {
  background: var(--surface); border: 1px solid var(--ring); border-radius: var(--radius);
  padding: 16px;
}
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }
.tile .v { font-size: 30px; font-weight: 600; letter-spacing: -.02em; }
.tile .k { color: var(--ink-2); font-size: 12.5px; margin-top: 2px; }
.tile .n { color: var(--muted); font-size: 11.5px; margin-top: 6px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
@media (max-width: 860px) { .grid2 { grid-template-columns: 1fr; } }
.chip {
  display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px; border-radius: 999px;
  font-size: 11.5px; font-weight: 600; border: 1px solid; white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.chip::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.chip.good { color: var(--good); border-color: color-mix(in srgb, var(--good) 45%, transparent); }
.chip.warning { color: var(--warning); border-color: color-mix(in srgb, var(--warning) 45%, transparent); }
.chip.serious { color: var(--serious); border-color: color-mix(in srgb, var(--serious) 45%, transparent); }
.chip.critical { color: var(--critical); border-color: color-mix(in srgb, var(--critical) 45%, transparent); }
.chip.neutral { color: var(--ink-2); border-color: var(--rule); }
.tabs { display: flex; gap: 4px; margin: 24px 0 14px; flex-wrap: wrap; border-bottom: 1px solid var(--grid); }
.tab {
  appearance: none; background: none; border: 0; border-bottom: 2px solid transparent;
  color: var(--ink-2); font: inherit; font-size: 14px; padding: 8px 12px; cursor: pointer;
}
.tab[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--series); font-weight: 600; }
.panel[hidden] { display: none; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th { text-align: left; color: var(--ink-2); font-weight: 600; font-size: 12px; padding: 8px 10px;
     border-bottom: 1px solid var(--rule); position: sticky; top: 0; background: var(--surface); }
td { padding: 9px 10px; border-bottom: 1px solid var(--grid); vertical-align: top; }
tbody tr { cursor: pointer; }
tbody tr:hover { background: color-mix(in srgb, var(--series) 7%, transparent); }
tbody tr[aria-selected="true"] { background: color-mix(in srgb, var(--series) 12%, transparent); }
.num { font-variant-numeric: tabular-nums; }
.controls { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
select, input[type="search"] {
  font: inherit; font-size: 13px; padding: 5px 8px; border-radius: 7px;
  border: 1px solid var(--rule); background: var(--surface); color: var(--ink);
}
.msg { border-left: 2px solid var(--grid); padding: 2px 0 14px 14px; margin-bottom: 4px; }
.msg-head { font-size: 12.5px; color: var(--ink-2); display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.msg-body { white-space: pre-wrap; font-size: 13.5px; margin-top: 6px; }
.msg.human { border-left-color: var(--critical); }
.mid { color: var(--muted); }
.detail { margin-top: 16px; }
.detail dt { color: var(--ink-2); font-size: 12px; margin-top: 12px; }
.detail dd { margin: 3px 0 0; font-size: 13.5px; white-space: pre-wrap; }
.path { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
.legend { color: var(--muted); font-size: 12px; margin-top: 10px; }
.bar-label { font-size: 12.5px; fill: var(--ink-2); }
.bar-value { font-size: 12.5px; fill: var(--ink); font-variant-numeric: tabular-nums; }
.node-label { font-size: 11.5px; fill: var(--ink); font-family: ui-monospace, Menlo, monospace; }
.edge-label { font-size: 11px; fill: var(--muted); font-variant-numeric: tabular-nums; }
.empty { color: var(--muted); font-size: 13.5px; padding: 12px 0; }
button.theme { appearance: none; background: none; border: 1px solid var(--rule); color: var(--ink-2);
  border-radius: 7px; padding: 5px 10px; font: inherit; font-size: 12.5px; cursor: pointer; }
.tooltip {
  position: fixed; pointer-events: none; background: var(--surface); color: var(--ink);
  border: 1px solid var(--ring); border-radius: 8px; padding: 7px 10px; font-size: 12.5px;
  box-shadow: 0 6px 20px rgba(0,0,0,.18); opacity: 0; transition: opacity .1s; z-index: 10;
  max-width: 260px;
}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1 id="title"></h1>
    <div class="sub" id="subtitle"></div>
  </div>
  <button class="theme" id="theme">Toggle theme</button>
</header>

<div class="tiles" id="tiles"></div>

<div class="grid2">
  <section class="card">
    <h2>What the author must do</h2>
    <svg id="actions" role="img" aria-label="Issue counts by required action"></svg>
    <div class="legend">One issue per row of the ledger; counts by the status the Editor assigned.</div>
  </section>
  <section class="card">
    <h2>Who moved whom</h2>
    <svg id="influence" role="img" aria-label="Influence graph: arrows point from the agent whose argument persuaded to the agent who changed position"></svg>
    <div class="legend" id="influence-note"></div>
  </section>
</div>

<div class="tabs" role="tablist" id="tabs"></div>
<div id="panels"></div>
</div>
<div class="tooltip" id="tip" role="status"></div>

<script id="payload" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("payload").textContent);
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const tip = $("tip");
function showTip(evt, html) {
  tip.innerHTML = html; tip.style.opacity = "1";
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.style.opacity = "0"; };

/* ---------------------------------------------------------------- header */
$("title").textContent = D.run.project + " — deliberation dashboard";
$("subtitle").innerHTML =
  `<span class="mono">${esc(D.run.run_id)}</span> · ${esc(D.run.manuscript)} ` +
  `<span class="mid">(sha256 ${esc(D.run.manuscript_hash)})</span>` +
  (D.run.journal ? ` · ${esc(D.run.journal)}` : "") +
  ` · ${D.run.rounds}/${D.run.max_rounds} rounds · ${esc(D.run.termination)}`;

$("theme").onclick = () => {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  document.documentElement.setAttribute("data-theme", dark ? "light" : "dark");
  drawActions(); drawInfluence();
};

/* ----------------------------------------------------------------- tiles */
const S = D.stats;
const tiles = [
  { v: S.issues, k: "issues on the ledger", n: S.open ? S.open + " still open" : "all terminal" },
  { v: S.new_analysis, k: "need new analysis", n: "the expensive column" },
  { v: S.changes, k: "position changes", n: "recorded with what caused them" },
  { v: S.unresolved, k: "unresolved disagreements", n: "left unresolved, deliberately" },
  { v: D.run.rounds, k: "rounds", n: "of " + D.run.max_rounds + " allowed" },
  { v: S.calls, k: "model calls", n: (S.tokens_in + S.tokens_out).toLocaleString() + " tokens" },
];
$("tiles").innerHTML = tiles.map((t) =>
  `<div class="card tile"><div class="v num">${t.v}</div><div class="k">${t.k}</div>
   <div class="n">${t.n}</div></div>`).join("");

/* ------------------------------------------------------- actions bar chart
   One series, so: single hue, no legend, direct value labels. */
function drawActions() {
  const svg = $("actions");
  const rows = D.action_counts;
  const rowH = 30, padL = 178, padR = 44, top = 6, w = svg.clientWidth || 520;
  const h = rows.length * rowH + top;
  const max = Math.max(1, ...rows.map((r) => r.count));
  const scale = (v) => Math.max(2, (v / max) * (w - padL - padR));
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("height", h);
  svg.innerHTML = rows.map((r, i) => {
    const y = top + i * rowH, bw = scale(r.count);
    return `<g class="bar" data-label="${esc(r.label)}" data-count="${r.count}">
      <text class="bar-label" x="${padL - 12}" y="${y + 18}" text-anchor="end">${esc(r.label)}</text>
      <rect x="${padL}" y="${y + 6}" width="${bw}" height="16" rx="4"
            fill="var(--series)"></rect>
      <text class="bar-value" x="${padL + bw + 8}" y="${y + 19}">${r.count}</text>
      <rect x="0" y="${y}" width="${w}" height="${rowH}" fill="transparent"></rect>
    </g>`;
  }).join("");
  svg.querySelectorAll("g.bar").forEach((g) => {
    g.addEventListener("mousemove", (e) => showTip(e,
      `<b>${esc(g.dataset.label)}</b><br>${g.dataset.count} issue(s)`));
    g.addEventListener("mouseleave", hideTip);
  });
}

/* ------------------------------------------------------- influence graph
   Agents are identified by label, not hue: five categorical colours cannot
   clear CVD separation when every pair can appear together. */
function drawInfluence() {
  const svg = $("influence");
  const edges = Object.entries(D.influence.edges);
  const order = ["Reviewer1", "Reviewer2", "Reviewer3", "Author", "Editor"];
  const present = order.filter((a) =>
    edges.some(([k]) => k.startsWith(a + " ") || k.endsWith("> " + a)));
  const nodes = present.length ? present : order;
  const w = svg.clientWidth || 520, h = 230, cx = w / 2, cy = h / 2 + 4;
  const r = Math.min(w, h) / 2 - 46;
  const pos = {};
  nodes.forEach((n, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / nodes.length;
    pos[n] = { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  });
  const maxW = Math.max(1, ...edges.map(([, v]) => v));
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("height", h);
  let out = `<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5"
    markerHeight="5" orient="auto-start-reverse">
    <path d="M0 0 L10 5 L0 10 z" fill="var(--series)"></path></marker></defs>`;
  if (!edges.length) {
    out += `<text x="${cx}" y="${cy}" text-anchor="middle" class="bar-label">
      No position changes were triggered by another participant.</text>`;
  }
  edges.forEach(([key, count]) => {
    const [from, to] = key.split(" -> ");
    if (!pos[from] || !pos[to]) return;
    const a = pos[from], b = pos[to];
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
    const pad = 26;
    const x1 = a.x + (dx / len) * pad, y1 = a.y + (dy / len) * pad;
    const x2 = b.x - (dx / len) * pad, y2 = b.y - (dy / len) * pad;
    const sw = 1.5 + (count / maxW) * 2.5;
    out += `<g class="edge" data-from="${esc(from)}" data-to="${esc(to)}" data-count="${count}">
      <line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--series)"
            stroke-width="${sw}" marker-end="url(#ah)" opacity=".85"></line>
      <line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="transparent"
            stroke-width="14"></line>
      ${maxW > 1 ? `<text class="edge-label" x="${(x1 + x2) / 2}"
            y="${(y1 + y2) / 2 - 6}" text-anchor="middle">${count}</text>` : ""}</g>`;
  });
  nodes.forEach((n) => {
    const p = pos[n];
    const moved = D.influence.updated[n] || 0, moving = D.influence.persuaded[n] || 0;
    out += `<g class="node" data-n="${esc(n)}" data-moved="${moved}" data-moving="${moving}">
      <circle cx="${p.x}" cy="${p.y}" r="24" fill="var(--surface)" stroke="var(--rule)"></circle>
      <text class="node-label" x="${p.x}" y="${p.y + 4}" text-anchor="middle">${esc(
        n.replace("Reviewer", "R"))}</text></g>`;
  });
  svg.innerHTML = out;
  svg.querySelectorAll("g.edge").forEach((g) => {
    g.addEventListener("mousemove", (e) => showTip(e,
      `<b>${esc(g.dataset.from)}</b> changed <b>${esc(g.dataset.to)}</b>'s position<br>` +
      `${g.dataset.count} time(s)`));
    g.addEventListener("mouseleave", hideTip);
  });
  svg.querySelectorAll("g.node").forEach((g) => {
    g.addEventListener("mousemove", (e) => showTip(e,
      `<b>${esc(g.dataset.n)}</b><br>persuaded others ${g.dataset.moving}×<br>` +
      `changed own position ${g.dataset.moved}×`));
    g.addEventListener("mouseleave", hideTip);
  });
}
$("influence-note").textContent = D.influence.invalid.length
  ? D.influence.invalid.length + " influence link(s) could not be resolved to a message."
  : "Arrows point from the argument to the participant it moved. Width is the number of changes.";

/* ------------------------------------------------------------------ tabs */
const TABS = [
  ["ledger", "Issue ledger"], ["plan", "Revision plan"], ["transcript", "Transcript"],
  ["influence", "Influence"], ["agents", "Agents & reproducibility"],
];
$("tabs").innerHTML = TABS.map(([id, label], i) =>
  `<button class="tab" role="tab" data-t="${id}" aria-selected="${i === 0}">${label}</button>`).join("");
$("panels").innerHTML = TABS.map(([id], i) =>
  `<div class="panel" id="p-${id}" role="tabpanel" ${i ? "hidden" : ""}></div>`).join("");
$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab"); if (!b) return;
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", t === b));
  TABS.forEach(([id]) => { $("p-" + id).hidden = id !== b.dataset.t; });
});

/* ---------------------------------------------------------------- ledger */
function statusChip(row) {
  return `<span class="chip ${row.tone}">${esc(row.status)}</span>`;
}
function renderLedger() {
  const statuses = [...new Set(D.issues.map((i) => i.status))].sort();
  $("p-ledger").innerHTML = `
    <div class="controls">
      <select id="f-status"><option value="">All statuses</option>
        ${statuses.map((s) => `<option>${esc(s)}</option>`).join("")}</select>
      <select id="f-agent"><option value="">Raised by anyone</option>
        ${["Reviewer1", "Reviewer2", "Reviewer3", "Editor"].map((a) =>
          `<option>${a}</option>`).join("")}</select>
      <input type="search" id="f-text" placeholder="Search titles and actions…" size="28">
    </div>
    <div class="card" style="padding:0;overflow:auto;max-height:520px">
      <table><thead><tr>
        <th>Issue</th><th>Status</th><th>Sev.</th><th>Priority</th><th>Raised by</th>
        <th>Required action</th></tr></thead><tbody id="ledger-body"></tbody></table>
    </div>
    <div class="detail card" id="issue-detail" style="margin-top:16px">
      <div class="empty">Select an issue to see its history, positions and influence path.</div>
    </div>`;
  const body = $("ledger-body");
  function paint() {
    const s = $("f-status").value, a = $("f-agent").value;
    const q = $("f-text").value.toLowerCase();
    const rows = D.issues.filter((r) =>
      (!s || r.status === s) && (!a || r.raised_by.includes(a)) &&
      (!q || (r.title + " " + r.action + " " + r.summary).toLowerCase().includes(q)));
    body.innerHTML = rows.length ? rows.map((r) => `
      <tr data-id="${r.id}">
        <td><span class="mono">${r.id}</span><br>${esc(r.title)}</td>
        <td>${statusChip(r)}</td><td>${esc(r.severity)}</td><td>${esc(r.priority)}</td>
        <td>${r.raised_by.map((x) => esc(x).replace("Reviewer", "R")).join(", ") || "—"}</td>
        <td>${esc(r.action ? (r.action.length > 160 ? r.action.slice(0, 158) + "…" : r.action) : "—")}</td>
      </tr>`).join("") : `<tr><td colspan="6" class="empty">No issues match.</td></tr>`;
  }
  ["f-status", "f-agent", "f-text"].forEach((id) =>
    $(id).addEventListener("input", paint));
  body.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return;
    body.querySelectorAll("tr").forEach((r) => r.setAttribute("aria-selected", r === tr));
    showIssue(tr.dataset.id);
  });
  paint();
}
function showIssue(id) {
  const r = D.issues.find((x) => x.id === id);
  const msgs = D.messages.filter((m) => m.issue_ids.includes(id));
  const pos = D.positions.filter((p) => p.issue_id === id);
  const positions = Object.entries(r.positions).map(([agent, p]) =>
    `<li><b>${esc(agent)}</b>: ${esc(p)}</li>`).join("") || "<li class='mid'>none recorded</li>";
  const history = pos.map((p) => `<li>Round ${p.round} — <b>${esc(p.agent)}</b> ` +
    (p.changed ? `${esc(p.from)} → ${esc(p.position)}` : `${esc(p.position)}`) +
    (p.trigger_id ? ` <span class="mid">(after M${p.trigger_id}${
      p.trigger_agent ? ", " + esc(p.trigger_agent) : ""})</span>` : "") +
    (p.rationale ? `<br><span class="mid">${esc(p.rationale.length > 300 ? p.rationale.slice(0, 298) + "…" : p.rationale)}</span>` : "") +
    "</li>").join("");
  $("issue-detail").innerHTML = `
    <h2>${esc(r.id)} — ${esc(r.title)}</h2>
    <div>${statusChip(r)} <span class="mid">${esc(r.classification)} · ${esc(r.category)} ·
      opened round ${r.created_round}${r.closed_round != null ? ", closed round " + r.closed_round : ""}</span></div>
    <dl>
      <dt>Manuscript location</dt><dd>${esc(r.location || "—")}</dd>
      <dt>Why it matters</dt><dd>${esc(r.why || "—")}</dd>
      <dt>Editor synthesis</dt><dd>${esc(r.summary || "—")}</dd>
      <dt>Required action</dt><dd>${esc(r.action || "—")}</dd>
      <dt>Editor decision</dt><dd>${esc(r.decision || "—")}</dd>
      ${r.analysis !== "no" ? `<dt>Analysis required (${esc(r.analysis)})</dt>
        <dd>${esc(r.analysis_detail || "—")}</dd>` : ""}
      <dt>Final positions</dt><dd><ul>${positions}</ul></dd>
      ${history ? `<dt>Position history</dt><dd><ul>${history}</ul></dd>` : ""}
      <dt>Influence path</dt><dd class="path">${esc(r.path || "no position changes")}</dd>
    </dl>
    <h2 style="margin-top:18px">What was said (${msgs.length})</h2>
    ${msgs.map(renderMessage).join("") || "<div class='empty'>Nothing tagged to this issue.</div>"}`;
  $("issue-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ------------------------------------------------------------ transcript */
function renderMessage(m) {
  return `<div class="msg ${m.human ? "human" : ""}">
    <div class="msg-head">
      <span class="mono">M${m.id}</span>
      <b>${esc(m.sender)}</b> <span class="mid">→ ${esc(m.recipients.join(", "))}</span>
      <span class="mid">round ${m.round} · ${esc(m.type)}</span>
      ${m.issue_ids.map((i) => `<span class="mono mid">${esc(i)}</span>`).join(" ")}
      ${m.private ? `<span class="chip neutral">private${
        m.disclosed != null ? ", disclosed r" + m.disclosed : ""}</span>` : ""}
      ${m.human ? `<span class="chip critical">human</span>` : ""}
    </div>
    <div class="msg-body">${esc(m.content)}</div>
  </div>`;
}
function renderTranscript() {
  const rounds = [...new Set(D.messages.map((m) => m.round))].sort((a, b) => a - b);
  const agents = [...new Set(D.messages.map((m) => m.sender))];
  $("p-transcript").innerHTML = `
    <div class="controls">
      <select id="t-round"><option value="">All rounds</option>
        ${rounds.map((r) => `<option value="${r}">Round ${r}</option>`).join("")}</select>
      <select id="t-agent"><option value="">Everyone</option>
        ${agents.map((a) => `<option>${esc(a)}</option>`).join("")}</select>
      <select id="t-issue"><option value="">Any issue</option>
        ${D.issues.map((i) => `<option>${i.id}</option>`).join("")}</select>
      <input type="search" id="t-text" placeholder="Search the transcript…" size="28">
    </div><div id="t-body"></div>`;
  function paint() {
    const r = $("t-round").value, a = $("t-agent").value, i = $("t-issue").value;
    const q = $("t-text").value.toLowerCase();
    const rows = D.messages.filter((m) =>
      (!r || String(m.round) === r) && (!a || m.sender === a) &&
      (!i || m.issue_ids.includes(i)) && (!q || m.content.toLowerCase().includes(q)));
    $("t-body").innerHTML = rows.map(renderMessage).join("") ||
      "<div class='empty'>No messages match.</div>";
  }
  ["t-round", "t-agent", "t-issue", "t-text"].forEach((id) =>
    $(id).addEventListener("input", paint));
  paint();
}

/* ------------------------------------------------------------------ plan */
function renderPlan() {
  const order = { essential: 0, important: 1, minor: 2 };
  const items = [...D.plan].sort((a, b) =>
    (order[a.priority] ?? 3) - (order[b.priority] ?? 3) || a.issue_id.localeCompare(b.issue_id));
  $("p-plan").innerHTML = items.map((p) => {
    const issue = D.issues.find((i) => i.id === p.issue_id) || { tone: "good", status: p.status };
    return `<div class="card" style="margin-bottom:12px">
      <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
        <span class="mono"><b>${esc(p.issue_id)}</b></span>
        <span class="chip ${issue.tone}">${esc(p.status)}</span>
        <span class="mid">${esc(p.priority)} · ${esc(p.classification)}</span>
      </div>
      <div style="margin-top:6px"><b>${esc(p.title)}</b></div>
      <dl class="detail">
        <dt>Exact required action</dt><dd>${esc(p.required_action || "—")}</dd>
        ${p.analysis_required !== "no" ? `<dt>Analysis (${esc(p.analysis_required)})</dt>
          <dd>${esc(p.analysis_detail || "—")}</dd>` : ""}
        ${p.textual_revision_required === "yes" ? `<dt>Textual revision</dt>
          <dd>${esc(p.textual_detail || "—")}</dd>` : ""}
        <dt>Where</dt><dd>${esc(p.manuscript_location || "—")}</dd>
      </dl></div>`;
  }).join("");
}

/* ------------------------------------------------------------- influence */
function renderInfluenceTab() {
  const withPath = D.issues.filter((i) => i.path && i.path !== "(no position changes)");
  $("p-influence").innerHTML = `
    <div class="card"><h2>Influence paths</h2>
    ${withPath.length ? withPath.map((i) => `<div style="margin-bottom:10px">
        <span class="mono"><b>${esc(i.id)}</b></span> ${esc(i.title)}<br>
        <span class="path">${esc(i.path)}</span></div>`).join("")
      : "<div class='empty'>No position changed during this run.</div>"}</div>
    <div class="card" style="margin-top:12px"><h2>Position changes</h2>
    ${D.positions.filter((p) => p.changed).map((p) => `<div style="margin-bottom:10px">
      <span class="mono">${esc(p.issue_id)}</span> · round ${p.round} ·
      <b>${esc(p.agent)}</b>: ${esc(p.from)} → ${esc(p.position)}
      ${p.trigger_id ? `<span class="mid">(triggered by M${p.trigger_id}${
        p.trigger_agent ? ", " + esc(p.trigger_agent) : ""})</span>` : ""}
      ${p.remaining && !/^(none|n\/a)$/i.test(p.remaining.trim())
        ? `<br><span class="mid">remaining concern: ${esc(p.remaining)}</span>` : ""}
      </div>`).join("") || "<div class='empty'>None.</div>"}</div>`;
}

/* ---------------------------------------------------------------- agents */
function renderAgents() {
  $("p-agents").innerHTML = `
    <div class="card"><table><thead><tr>
      <th>Agent</th><th>Role</th><th>Provider</th><th>Model</th><th>Expertise</th>
    </tr></thead><tbody>
    ${D.agents.map((a) => `<tr style="cursor:default">
      <td class="mono">${esc(a.id)}</td><td>${esc(a.role)}</td><td>${esc(a.provider)}</td>
      <td class="mono">${esc(a.model)}</td><td>${esc(a.expertise || "—")}</td></tr>`).join("")}
    </tbody></table></div>
    <div class="card" style="margin-top:12px">
      <h2>Reproducibility</h2>
      <dl class="detail">
        <dt>Run id</dt><dd class="mono">${esc(D.run.run_id)}</dd>
        <dt>Manuscript</dt><dd>${esc(D.run.manuscript)} <span class="mid">sha256 ${
          esc(D.run.manuscript_hash)}</span></dd>
        <dt>Started / ended</dt><dd>${esc(D.run.started_at)} → ${esc(D.run.ended_at || "—")}</dd>
        <dt>Termination</dt><dd>${esc(D.run.termination)}</dd>
        <dt>Model calls</dt><dd>${D.stats.calls} calls ·
          ${D.stats.tokens_in.toLocaleString()} in / ${D.stats.tokens_out.toLocaleString()} out</dd>
        <dt>peerreview</dt><dd>v${esc(D.run.version)}</dd>
      </dl>
      <div class="legend">Full prompts, parameters and tool calls are in the run's SQLite
      database; this page is a read-only view of it.</div>
    </div>`;
}

renderLedger(); renderPlan(); renderTranscript(); renderInfluenceTab(); renderAgents();
drawActions(); drawInfluence();
addEventListener("resize", () => { drawActions(); drawInfluence(); });
</script>
</body>
</html>
"""
