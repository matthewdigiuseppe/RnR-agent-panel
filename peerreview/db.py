"""SQLite persistence layer -- the canonical state of a run.

Design rules enforced here rather than in prompts:

* Messages are append-only. A private message becomes visible to everyone by
  recording the round at which it was disclosed, never by rewriting it.
* Every issue mutation writes an `issue_events` row, so the ledger's history is
  reconstructible even though `issues` holds the current projection.
* Reopening a closed issue requires a reason.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import __version__
from .models import ALL, STATUS_OPEN, is_terminal
from .util import jdump, jload, utcnow

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class ReopenWithoutReason(ValueError):
    """Raised when a terminal/closed issue is reopened without justification."""


class Store:
    """Thread-safe (connection-per-thread) SQLite store."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    # ---------------------------------------------------------------- plumbing
    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def init_schema(self) -> None:
        with self._write_lock:
            self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(runs)")}
        if "completed_round" not in columns:
            self.conn.execute(
                "ALTER TABLE runs ADD COLUMN completed_round INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _exec(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._write_lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    # -------------------------------------------------------------------- runs
    def create_run(self, *, run_id: str, project: str, manuscript: str,
                   manuscript_hash: str, inputs: Any, journal: str | None,
                   config: Any, model_config: Any, max_rounds: int) -> str:
        self._exec(
            """INSERT INTO runs (run_id, project, manuscript, manuscript_hash, inputs_json,
                                 journal, config_json, model_config_json, max_rounds,
                                 started_at, status, phase, peerreview_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,'created','init',?)""",
            (run_id, project, manuscript, manuscript_hash, jdump(inputs), journal,
             jdump(config), jdump(model_config), max_rounds, utcnow(), __version__),
        )
        return run_id

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM runs WHERE run_id=?", (run_id,))

    def latest_run(self, project: str | None = None) -> sqlite3.Row | None:
        if project:
            return self.query_one(
                "SELECT * FROM runs WHERE project=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                (project,))
        return self.query_one("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1")

    def list_runs(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM runs ORDER BY started_at DESC")

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{key}=?" for key in fields)
        self._exec(f"UPDATE runs SET {cols} WHERE run_id=?", (*fields.values(), run_id))

    def run_status(self, run_id: str) -> str:
        row = self.query_one("SELECT status FROM runs WHERE run_id=?", (run_id,))
        return row["status"] if row else "unknown"

    # ------------------------------------------------------------------ agents
    def upsert_agent(self, run_id: str, spec) -> None:
        self._exec(
            """INSERT INTO agents (id, run_id, role, name, expertise, system_prompt,
                                   provider, model, params_json, status)
               VALUES (?,?,?,?,?,?,?,?,?,'idle')
               ON CONFLICT(run_id, id) DO UPDATE SET
                 role=excluded.role, name=excluded.name, expertise=excluded.expertise,
                 system_prompt=excluded.system_prompt, provider=excluded.provider,
                 model=excluded.model, params_json=excluded.params_json""",
            (spec.id, run_id, spec.role, spec.name, spec.expertise, spec.system_prompt,
             spec.provider, spec.model, jdump(spec.params)),
        )

    def get_agents(self, run_id: str) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM agents WHERE run_id=? ORDER BY rowid", (run_id,))

    def set_agent_status(self, run_id: str, agent_id: str, status: str) -> None:
        self._exec("UPDATE agents SET status=? WHERE run_id=? AND id=?", (status, run_id, agent_id))

    # ---------------------------------------------------------------- messages
    def add_message(self, *, run_id: str, round: int, phase: str, sender: str,
                    recipients: Iterable[str], content: str, message_type: str,
                    issue_ids: Iterable[str] | None = None, visibility: str = "shared",
                    is_human: bool = False, meta: Any = None,
                    disclosed_at_round: int | None = None) -> int:
        recipients = list(recipients) or [ALL]
        cur = self._exec(
            """INSERT INTO messages (run_id, timestamp, round, phase, sender, recipients,
                                     issue_ids, message_type, content, visibility,
                                     disclosed_at_round, is_human, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, utcnow(), round, phase, sender, jdump(recipients),
             jdump(list(issue_ids or [])), message_type, content, visibility,
             disclosed_at_round, 1 if is_human else 0, jdump(meta) if meta is not None else None),
        )
        return int(cur.lastrowid)

    def get_message(self, run_id: str, message_id: int) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM messages WHERE run_id=? AND id=?", (run_id, message_id))

    def all_messages(self, run_id: str) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM messages WHERE run_id=? ORDER BY id", (run_id,))

    def visible_messages(self, run_id: str, agent_id: str, *, as_of_round: int | None = None,
                         limit: int | None = None) -> list[sqlite3.Row]:
        """Messages `agent_id` is entitled to see.

        A message is visible when it is shared, when the agent sent it, when the
        agent is a recipient, or when it has been explicitly disclosed at or
        before `as_of_round`. This is the single enforcement point for the
        no-herding guarantee in Phase 1.
        """
        rows = self.query("SELECT * FROM messages WHERE run_id=? ORDER BY id", (run_id,))
        visible: list[sqlite3.Row] = []
        for row in rows:
            if as_of_round is not None and row["round"] > as_of_round:
                continue
            if self._is_visible(row, agent_id, as_of_round):
                visible.append(row)
        if limit is not None:
            visible = visible[-limit:]
        return visible

    @staticmethod
    def _is_visible(row: sqlite3.Row, agent_id: str, as_of_round: int | None) -> bool:
        if row["sender"] == agent_id:
            return True
        recipients = jload(row["recipients"], []) or []
        if agent_id in recipients or ALL in recipients:
            return True
        if row["visibility"] == "shared":
            return True
        disclosed = row["disclosed_at_round"]
        if disclosed is not None and (as_of_round is None or as_of_round >= disclosed):
            return True
        return False

    def disclose_messages(self, run_id: str, round: int, *, phase: str | None = None,
                          message_ids: Sequence[int] | None = None) -> int:
        """Make previously private messages visible to everyone from `round` on.

        History is preserved: only the disclosure round is recorded.
        """
        with self._write_lock:
            if message_ids is not None:
                marks = ",".join("?" for _ in message_ids)
                cur = self.conn.execute(
                    f"""UPDATE messages SET disclosed_at_round=?
                        WHERE run_id=? AND disclosed_at_round IS NULL AND id IN ({marks})""",
                    (round, run_id, *message_ids))
            elif phase is not None:
                cur = self.conn.execute(
                    """UPDATE messages SET disclosed_at_round=?
                       WHERE run_id=? AND phase=? AND visibility='private'
                         AND disclosed_at_round IS NULL""",
                    (round, run_id, phase))
            else:
                cur = self.conn.execute(
                    """UPDATE messages SET disclosed_at_round=?
                       WHERE run_id=? AND visibility='private' AND disclosed_at_round IS NULL""",
                    (round, run_id))
            self.conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ issues
    def next_issue_number(self, run_id: str, prefix: str = "ISSUE-") -> int:
        rows = self.query(
            "SELECT issue_id FROM issues WHERE run_id=? AND issue_id LIKE ?", (run_id, prefix + "%"))
        numbers = []
        for row in rows:
            tail = row["issue_id"][len(prefix):]
            if tail.isdigit():
                numbers.append(int(tail))
        return (max(numbers) + 1) if numbers else 1

    def create_issue(self, run_id: str, *, issue_id: str, title: str, actor: str, round: int,
                     description: str = "", category: str | None = None,
                     severity: str | None = None, status: str = STATUS_OPEN,
                     raised_by: Iterable[str] = (), manuscript_location: str = "",
                     original_comment: str = "", current_summary: str = "",
                     provisional: bool = False, confidence: str | None = None,
                     message_id: int | None = None) -> str:
        self._exec(
            """INSERT OR REPLACE INTO issues (issue_id, run_id, title, description, category,
                    severity, status, raised_by, manuscript_location, original_comment,
                    current_summary, created_round, provisional, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (issue_id, run_id, title, description, category, severity, status,
             jdump(list(raised_by)), manuscript_location, original_comment, current_summary,
             round, 1 if provisional else 0, confidence),
        )
        self.add_issue_event(run_id, issue_id, round, actor, "created",
                             payload={"title": title, "status": status}, message_id=message_id)
        return issue_id

    def get_issue(self, run_id: str, issue_id: str) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM issues WHERE run_id=? AND issue_id=?", (run_id, issue_id))

    def list_issues(self, run_id: str, *, provisional: bool | None = False,
                    include_merged: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM issues WHERE run_id=?"
        params: list[Any] = [run_id]
        if provisional is not None:
            sql += " AND provisional=?"
            params.append(1 if provisional else 0)
        if not include_merged:
            sql += " AND merged_into IS NULL"
        sql += " ORDER BY issue_id"
        return self.query(sql, params)

    def open_issues(self, run_id: str) -> list[sqlite3.Row]:
        return [row for row in self.list_issues(run_id) if not is_terminal(row["status"])]

    def update_issue(self, run_id: str, issue_id: str, *, actor: str, round: int,
                     reason: str | None = None, message_id: int | None = None,
                     allow_reopen: bool = False, **fields: Any) -> sqlite3.Row | None:
        """Update issue fields, writing an append-only audit event.

        Moving an issue out of a terminal status counts as a reopen and requires
        both `allow_reopen` and a non-empty `reason`.
        """
        current = self.get_issue(run_id, issue_id)
        if current is None:
            return None
        new_status = fields.get("status")
        reopening = bool(new_status) and is_terminal(current["status"]) and not is_terminal(new_status)
        if reopening:
            if not allow_reopen or not (reason or "").strip():
                raise ReopenWithoutReason(
                    f"{issue_id} is {current['status']}; reopening requires an explicit reason")
        if not fields:
            return current
        for key in fields:
            if key not in current.keys():
                raise KeyError(f"unknown issue field: {key}")
        cols = ", ".join(f"{key}=?" for key in fields)
        self._exec(f"UPDATE issues SET {cols} WHERE run_id=? AND issue_id=?",
                   (*fields.values(), run_id, issue_id))
        event_type = "reopen" if reopening else ("status_change" if new_status else "field_update")
        self.add_issue_event(run_id, issue_id, round, actor, event_type, reason=reason,
                             payload={"before": {k: current[k] for k in fields}, "after": fields},
                             message_id=message_id)
        return self.get_issue(run_id, issue_id)

    def merge_issue(self, run_id: str, *, source_id: str, target_id: str, actor: str,
                    round: int, reason: str = "", message_id: int | None = None) -> None:
        """Fold `source_id` into `target_id`, preserving the source row verbatim."""
        source = self.get_issue(run_id, source_id)
        target = self.get_issue(run_id, target_id)
        if source is None or target is None:
            raise KeyError(f"cannot merge {source_id} -> {target_id}: missing issue")
        raised = jload(target["raised_by"], []) or []
        for agent in jload(source["raised_by"], []) or []:
            if agent not in raised:
                raised.append(agent)
        merged_comment = (target["original_comment"] or "")
        source_comment = (source["original_comment"] or "").strip()
        if source_comment and source_comment not in merged_comment:
            merged_comment = (merged_comment + "\n\n--- merged from " + source_id + " ---\n"
                              + source_comment).strip()
        self._exec("UPDATE issues SET merged_into=? WHERE run_id=? AND issue_id=?",
                   (target_id, run_id, source_id))
        self._exec("UPDATE issues SET raised_by=?, original_comment=? WHERE run_id=? AND issue_id=?",
                   (jdump(raised), merged_comment, run_id, target_id))
        self.add_issue_event(run_id, source_id, round, actor, "merge", reason=reason,
                             payload={"merged_into": target_id}, message_id=message_id)
        self.add_issue_event(run_id, target_id, round, actor, "merge", reason=reason,
                             payload={"absorbed": source_id}, message_id=message_id)

    def add_issue_event(self, run_id: str, issue_id: str, round: int, actor: str,
                        event_type: str, *, reason: str | None = None, payload: Any = None,
                        message_id: int | None = None) -> int:
        cur = self._exec(
            """INSERT INTO issue_events (run_id, issue_id, round, actor, event_type,
                                         reason, payload_json, message_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, issue_id, round, actor, event_type, reason, jdump(payload) if payload else None,
             message_id, utcnow()),
        )
        return int(cur.lastrowid)

    def issue_events(self, run_id: str, issue_id: str | None = None) -> list[sqlite3.Row]:
        if issue_id:
            return self.query(
                "SELECT * FROM issue_events WHERE run_id=? AND issue_id=? ORDER BY id",
                (run_id, issue_id))
        return self.query("SELECT * FROM issue_events WHERE run_id=? ORDER BY id", (run_id,))

    # --------------------------------------------------------------- positions
    def add_position(self, run_id: str, *, issue_id: str, round: int, agent: str, position: str,
                     changed_from: str | None = None, rationale: str = "",
                     triggered_by_message_id: int | None = None,
                     triggered_by_agent: str | None = None,
                     remaining_concern: str = "") -> int:
        cur = self._exec(
            """INSERT INTO issue_positions (run_id, issue_id, round, agent, position, changed_from,
                        rationale, triggered_by_message_id, triggered_by_agent, remaining_concern,
                        created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, issue_id, round, agent, position, changed_from, rationale,
             triggered_by_message_id, triggered_by_agent, remaining_concern, utcnow()),
        )
        return int(cur.lastrowid)

    def positions(self, run_id: str, issue_id: str | None = None,
                  agent: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM issue_positions WHERE run_id=?"
        params: list[Any] = [run_id]
        if issue_id:
            sql += " AND issue_id=?"
            params.append(issue_id)
        if agent:
            sql += " AND agent=?"
            params.append(agent)
        sql += " ORDER BY id"
        return self.query(sql, params)

    def latest_position(self, run_id: str, issue_id: str, agent: str) -> str | None:
        row = self.query_one(
            """SELECT position FROM issue_positions WHERE run_id=? AND issue_id=? AND agent=?
               ORDER BY id DESC LIMIT 1""", (run_id, issue_id, agent))
        return row["position"] if row else None

    def position_changes_in_round(self, run_id: str, round: int) -> list[sqlite3.Row]:
        return self.query(
            """SELECT * FROM issue_positions WHERE run_id=? AND round=?
               AND changed_from IS NOT NULL AND changed_from <> position ORDER BY id""",
            (run_id, round))

    # -------------------------------------------------------------- llm_calls
    def log_call(self, run_id: str, *, agent_id: str, round: int, phase: str, provider: str,
                 model: str, params: Any, system_prompt: str, messages: Any,
                 response_text: str | None, usage: Any = None, latency_ms: int | None = None,
                 tool_calls: Any = None, error: str | None = None) -> int:
        cur = self._exec(
            """INSERT INTO llm_calls (run_id, agent_id, round, phase, provider, model, params_json,
                        system_prompt, messages_json, response_text, tool_calls_json, usage_json,
                        latency_ms, error, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, agent_id, round, phase, provider, model, jdump(params), system_prompt,
             jdump(messages), response_text, jdump(tool_calls) if tool_calls else None,
             jdump(usage) if usage else None, latency_ms, error, utcnow()),
        )
        return int(cur.lastrowid)

    def calls(self, run_id: str) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM llm_calls WHERE run_id=? ORDER BY id", (run_id,))

    # ---------------------------------------------------------- interventions
    def add_intervention(self, run_id: str, *, round: int, kind: str, speak_as: str | None = None,
                         issue_id: str | None = None, payload: Any = None,
                         message_id: int | None = None) -> int:
        cur = self._exec(
            """INSERT INTO interventions (run_id, round, kind, speak_as, issue_id, payload_json,
                                          message_id, consumed, created_at)
               VALUES (?,?,?,?,?,?,?,0,?)""",
            (run_id, round, kind, speak_as, issue_id, jdump(payload) if payload else None,
             message_id, utcnow()),
        )
        return int(cur.lastrowid)

    def pending_interventions(self, run_id: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM interventions WHERE run_id=? AND consumed=0 ORDER BY id", (run_id,))

    def all_interventions(self, run_id: str) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM interventions WHERE run_id=? ORDER BY id", (run_id,))

    def link_intervention_message(self, run_id: str, intervention_id: int, message_id: int) -> None:
        self._exec("UPDATE interventions SET message_id=? WHERE run_id=? AND id=?",
                   (message_id, run_id, intervention_id))

    def consume_intervention(self, run_id: str, intervention_id: int) -> None:
        self._exec("UPDATE interventions SET consumed=1 WHERE run_id=? AND id=?",
                   (run_id, intervention_id))
