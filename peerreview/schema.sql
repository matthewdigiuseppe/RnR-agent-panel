-- Canonical state for peerreview. SQLite is the source of truth: every run is
-- fully reconstructible from this database alone (no ephemeral context).
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    project             TEXT NOT NULL,
    manuscript          TEXT NOT NULL,          -- primary manuscript path
    manuscript_hash     TEXT,                   -- sha256 of manuscript bytes
    inputs_json         TEXT,                   -- all input files + hashes
    journal             TEXT,
    config_json         TEXT,                   -- full resolved configuration
    model_config_json   TEXT,                   -- provider/model/params per agent
    max_rounds          INTEGER NOT NULL DEFAULT 20,
    started_at          TEXT,
    ended_at            TEXT,
    status              TEXT NOT NULL DEFAULT 'created',  -- created|running|paused|finished|failed
    phase               TEXT NOT NULL DEFAULT 'init',     -- init|independent_review|consolidation|deliberation|export|revision|done
    current_round       INTEGER NOT NULL DEFAULT 0,
    completed_round     INTEGER NOT NULL DEFAULT 0,  -- last round that finished

    termination_reason  TEXT,
    peerreview_version  TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    id            TEXT NOT NULL,                -- stable identity, e.g. "Reviewer2"
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    role          TEXT NOT NULL,                -- author|reviewer|editor
    name          TEXT NOT NULL,
    expertise     TEXT,
    system_prompt TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    params_json   TEXT,
    status        TEXT NOT NULL DEFAULT 'idle', -- idle|working|done|error
    PRIMARY KEY (run_id, id)
);

-- Append-only message bus. Nothing here is ever updated except disclosure,
-- which is recorded as a round number rather than by rewriting the row.
CREATE TABLE IF NOT EXISTS messages (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL REFERENCES runs(run_id),
    timestamp          TEXT NOT NULL,
    round              INTEGER NOT NULL,
    phase              TEXT NOT NULL,
    sender             TEXT NOT NULL,           -- agent id, or HUMAN
    recipients         TEXT NOT NULL,           -- JSON list of agent ids, or ["ALL"]
    issue_ids          TEXT,                    -- JSON list of issue ids (may be empty)
    message_type       TEXT NOT NULL,
    content            TEXT NOT NULL,
    visibility         TEXT NOT NULL DEFAULT 'shared',  -- shared|private
    disclosed_at_round INTEGER,                 -- when a private message became visible to all
    is_human           INTEGER NOT NULL DEFAULT 0,
    meta_json          TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_run ON messages(run_id, id);

CREATE TABLE IF NOT EXISTS issues (
    issue_id            TEXT NOT NULL,
    run_id              TEXT NOT NULL REFERENCES runs(run_id),
    title               TEXT NOT NULL,
    description         TEXT,
    category            TEXT,
    severity            TEXT,
    status              TEXT NOT NULL DEFAULT 'OPEN',
    raised_by           TEXT,                   -- JSON list of agent ids
    manuscript_location TEXT,
    original_comment    TEXT,                   -- verbatim originating text, never rewritten
    current_summary     TEXT,
    required_action     TEXT,
    editor_decision     TEXT,
    created_round       INTEGER,
    closed_round        INTEGER,
    merged_into         TEXT,                   -- canonical issue id if this was merged away
    provisional         INTEGER NOT NULL DEFAULT 0,
    priority            TEXT,
    confidence          TEXT,
    PRIMARY KEY (run_id, issue_id)
);

-- How each participant's position on each issue evolves, and what moved it.
CREATE TABLE IF NOT EXISTS issue_positions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  TEXT NOT NULL REFERENCES runs(run_id),
    issue_id                TEXT NOT NULL,
    round                   INTEGER NOT NULL,
    agent                   TEXT NOT NULL,
    position                TEXT NOT NULL,
    changed_from            TEXT,
    rationale               TEXT,
    triggered_by_message_id INTEGER REFERENCES messages(id),
    triggered_by_agent      TEXT,
    remaining_concern       TEXT,
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_issue ON issue_positions(run_id, issue_id, id);

-- Append-only audit of every mutation applied to an issue.
CREATE TABLE IF NOT EXISTS issue_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    issue_id     TEXT NOT NULL,
    round        INTEGER NOT NULL,
    actor        TEXT NOT NULL,
    event_type   TEXT NOT NULL,     -- created|status_change|merge|split|reopen|field_update|close
    reason       TEXT,
    payload_json TEXT,
    message_id   INTEGER REFERENCES messages(id),
    created_at   TEXT NOT NULL
);

-- Full reproducibility log: every prompt sent and response received.
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    agent_id      TEXT NOT NULL,
    round         INTEGER NOT NULL,
    phase         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    params_json   TEXT,
    system_prompt TEXT,
    messages_json TEXT,
    response_text TEXT,
    tool_calls_json TEXT,
    usage_json    TEXT,
    latency_ms    INTEGER,
    error         TEXT,
    created_at    TEXT NOT NULL
);

-- Human interventions are stored distinctly from agent messages.
CREATE TABLE IF NOT EXISTS interventions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    round        INTEGER NOT NULL,
    kind         TEXT NOT NULL,    -- message|correction|evidence|reopen|focus|pause|resume
    speak_as     TEXT,             -- identity the human speaks as (e.g. Author)
    issue_id     TEXT,
    payload_json TEXT,
    message_id   INTEGER REFERENCES messages(id),
    consumed     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
