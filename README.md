# peerreview

A local multi-agent system that simulates a structured editorial-board deliberation
around an academic manuscript: one Author, three Reviewers, and an Editor who chairs
the discussion and maintains a canonical issue ledger.

The output is an **operational revision plan**, not another referee report: what
substantively needs to change, what new analysis is actually required, what only needs
clearer explanation, which objections were withdrawn after rebuttal, and what remains
genuinely unresolved.

```
peerreview demo          # complete scripted run, no API keys, no cost
```

## What it does

**Phase 1 — independent review.** The three reviewers evaluate the manuscript without
sight of one another. This is enforced in the database, not by prompting: a message is
visible to an agent only if it sent it, received it, is shared, or has been explicitly
disclosed. Each reviewer emits discrete structured issues rather than a long report.

**Phase 2 — consolidation.** The Editor sees all three reviews, merges duplicates,
preserves meaningful disagreement, and assigns canonical issue ids. A minority concern
that only one reviewer raised is never dropped; if the Editor forgets one, it is carried
forward automatically.

**Phase 3 — shared deliberation.** The full history is disclosed to everyone. The Editor
sets an agenda each round, calls on specific participants, and updates the ledger.
Reviewers challenge each other, defer to the reviewer with the relevant expertise, change
position, or explicitly maintain a disagreement. The Author defends defensible choices,
corrects misunderstandings, concedes real weaknesses, and proposes minimal concrete
revisions.

Consensus is not the goal. `UNRESOLVED DISAGREEMENT` is a legitimate terminal state.

## Influence tracking

Every position change is structured and links to the message that caused it:

```
<<<POSITION CHANGE>>>
ISSUE: ISSUE-002
PREVIOUS POSITION: NEW ANALYSIS REQUIRED
NEW POSITION: EXPOSITION ONLY
REASON: @Reviewer2 showed a mediation analysis identifies a decomposition under
  untestable sequential ignorability, not the mechanism.
TRIGGERED BY: @Reviewer2
REMAINING CONCERN: the qualification must appear in the abstract
<<<END POSITION CHANGE>>>
```

which produces influence paths in `07_influence_report.md`:

```
ISSUE-002  Reviewer2 -> Reviewer3 -> Author -> Editor decision
```

Links are validated against the message table: a trigger that cannot be resolved to a
real message is dropped rather than invented.

## Install

```bash
git clone <this repo> && cd RnR-agent-panel
pip install -e .            # core is stdlib + PyYAML
pip install -e '.[pdf]'     # optional: PDF manuscripts (pypdf)
pip install -e '.[dev]'     # optional: pytest
```

Python 3.10+. For PDFs, any one of `pypdf`, `pdfminer.six`, `PyMuPDF` or poppler's
`pdftotext` will do; page numbers are preserved as `[p. N]` markers so agents can cite them.

## Run a real paper

```bash
peerreview init austerity-paper
cd austerity-paper
cp ~/papers/manuscript.pdf papers/
cp ~/papers/appendix.pdf papers/
$EDITOR peerreview.yaml            # set manuscript/appendix paths, journal, models

export ANTHROPIC_API_KEY=...       # and/or OPENAI_API_KEY

peerreview run . --dry-run         # check parsing, sections, agents: no model calls
peerreview run . -v                # the real thing
```

Then:

```bash
peerreview status .                        # where the run stands
peerreview issues . --open                 # the live ledger
peerreview transcript . --issue ISSUE-003  # everything said about one issue
peerreview export .                        # (re)write the outputs
```

A run costs roughly 30-60 model calls for a paper with 12-15 issues: three reviews, one
consolidation, then two Editor turns plus a handful of participant turns per round.

### Outputs

```
runs/2026-09-18-austerity-paper/
  01_initial_reviews.md              the three independent reviews, before any contact
  02_issue_ledger_initial.md         the ledger as it stood when deliberation opened
  03_deliberation_transcript.md      every message, with citable ids
  04_issue_ledger_final.md           final statuses, positions, editor decisions
  05_revision_plan.md                ← the deliverable
  06_unresolved_disagreements.md     what the panel did not settle, and why
  07_influence_report.md             who changed whose mind
  08_machine_readable_results.json   everything above, structured
  09_revised_manuscript.md           optional stage, with a per-issue change log
  10_response_to_reviewers.md        optional stage
```

### Intervening

You can step in at any round boundary; the run resumes from SQLite.

```bash
peerreview intervene . --pause
peerreview intervene . --message "The event study is in Appendix A1." --as Author --issue ISSUE-001
peerreview intervene . --correction --message "Reviewer3 misread Table 3: it is a tercile split."
peerreview intervene . --reopen ISSUE-004 --reason "New data the panel has not seen."
peerreview intervene . --focus ISSUE-002,ISSUE-003
peerreview resume .
```

Human input is stored distinctly (`interventions` table, `is_human` on messages) and is
never merged into agent history. Nothing is ever overwritten.

## Configuration

`peerreview.yaml` (TOML also works). Any agent can use any provider:

```yaml
project:
  manuscript: papers/manuscript.pdf
  appendix: papers/appendix.pdf
  journal: British Journal of Political Science

deliberation:
  max_rounds: 20
  stable_rounds_before_stop: 2        # stop after N rounds with no position change
  intervention_word_limit: 350

execution:
  isolation: thread                   # serial | thread | process

defaults:
  provider: anthropic
  model: claude-sonnet-5

agents:
  author:    {provider: anthropic, model: claude-opus-5}
  reviewer1: {specialization: substantive}
  reviewer2: {specialization: methods, provider: anthropic, model: claude-opus-5}
  reviewer3: {specialization: generalist, provider: openai, model: gpt-4o}
  editor:    {provider: anthropic, model: claude-sonnet-5}
```

Reviewer expertise and system prompts are overridable per agent
(`expertise:`, `system_prompt:`, `system_prompt_file:`).

### Providers

`anthropic`, `openai` (or any OpenAI-compatible gateway via `base_url`), `claude_cli`
(shells out to a local authenticated `claude` install — no API key needed), and `mock`
(deterministic, for tests). Orchestration never imports a provider SDK; adding one means
implementing `AgentBackend.send()` and calling `register_backend`:

```python
from peerreview.backends import AgentBackend, BackendResponse, register_backend

class MyBackend(AgentBackend):
    provider = "mine"
    def _send(self, messages, system_prompt, tools):
        ...
        return BackendResponse(text=..., provider=self.provider, model=self.model)

register_backend("mine", MyBackend)
```

## Architecture

```
peerreview/
  schema.sql      runs, agents, messages, issues, issue_positions, issue_events,
                  llm_calls, interventions
  db.py           the only place visibility and reopen rules are enforced
  manuscript.py   md/pdf -> sections, page numbers, table/figure anchors, retrieval
  backends/       AgentBackend + anthropic / openai / claude_cli / mock
  agents.py       isolated agent runtimes; serial, thread or process execution
  context.py      token-budgeted context assembly
  prompts.py      role prompts and per-phase instructions
  parsing.py      ISSUE / POSITION CHANGE / AGENDA / LEDGER UPDATE / MERGE / REVISION
  ledger.py       applies editor moves; records position changes and their triggers
  orchestrator.py phases, rounds, termination
  influence.py    influence edges, paths and aggregates
  exports.py      the eight outputs
  revision.py     optional stages 09 and 10
  cli.py          init / run / resume / status / issues / transcript / export / intervene / demo
```

SQLite is the canonical state: complete prompts, model names and parameters, every
message, manuscript hashes, configuration and run id are all persisted, so runs are
reproducible and resumable. `Ctrl-C` at any point leaves a resumable run.

### Designed against known failure modes

| Failure mode | Mitigation |
|---|---|
| Herding | Phase 1 independence enforced by the visibility layer, not by prompting |
| Sycophancy | Disagreement is explicitly legitimate; `UNRESOLVED DISAGREEMENT` is terminal |
| Endless discussion | Terminal statuses, editor control, stability and max-round stopping |
| Repetition | Full transcript in context + instruction not to restate; settled issues collapse to one-line summaries |
| Context explosion | Relevant manuscript sections + focused issue history + recent messages, under a token budget |
| Hallucination | Required manuscript locations; "the manuscript says X" / "I infer X" / "I recommend X"; uncited criticisms are flagged |
| Fake consensus | The Editor is instructed never to close an issue to tidy the ledger; positions are recorded per agent, not collectively |

## Tests

```bash
python -m pytest            # 75 tests, deterministic mock models, no API calls
```

Covering: phase-1 invisibility; full disclosure at phase 3; reviewer-to-reviewer
exchange; merge preserving original comments; position-change logging; influence links
resolving to real messages; reopen requiring a reason; resume after interruption; human
interventions; terminal statuses and stopping rules; provider swappability; and the
revision plan containing every non-dismissed issue.

## Writing your own mock scripts

`examples/demo/make_mock_script.py` shows how: rules match against the prompt header
(`AGENT:`, `PHASE:`, `ROUND:`, `ISSUES ON THE TABLE:`), so you can script an entire
deliberation deterministically and test orchestration changes for free:

```bash
python3 examples/demo/make_mock_script.py
peerreview run myproject --mock-script examples/demo/mock_script.json
```
