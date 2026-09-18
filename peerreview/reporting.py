"""Console progress output. Quiet by default, full transcripts under --verbose."""
from __future__ import annotations

import sys
from typing import Iterable, TextIO


class Reporter:
    def __init__(self, *, verbose: bool = False, quiet: bool = False,
                 stream: TextIO | None = None):
        self.verbose = verbose
        self.quiet = quiet
        self.stream = stream or sys.stdout
        self._round: int | None = None

    def _write(self, text: str) -> None:
        if self.quiet:
            return
        print(text, file=self.stream, flush=True)

    def phase(self, name: str) -> None:
        self._write(f"\n=== {name} ===")

    def round_header(self, number: int) -> None:
        self._round = number
        self._write(f"\n[Round {number}]")

    def info(self, text: str) -> None:
        self._write(f"  {text}")

    def turn(self, sender: str, recipients: Iterable[str], issue_ids: Iterable[str],
             content: str, *, words: int | None = None) -> None:
        recipients = list(recipients)
        issues = ", ".join(issue_ids)
        target = ", ".join(recipients)
        suffix = f" [{issues}]" if issues else ""
        size = f" ({words}w)" if words is not None else ""
        self._write(f"  {sender} -> {target}{suffix}{size}")
        if self.verbose:
            body = "\n".join("      " + line for line in content.strip().splitlines())
            self._write(body)

    def position(self, agent: str, issue_id: str, before: str | None, after: str,
                 trigger: str | None = None) -> None:
        arrow = f"{before or 'n/a'} -> {after}"
        tail = f"  (triggered by {trigger})" if trigger else ""
        self._write(f"  {agent} changes position on {issue_id}:\n    {arrow}{tail}")

    def ledger(self, issue_id: str, detail: str) -> None:
        self._write(f"  Editor updates {issue_id}: {detail}")

    def event(self, text: str) -> None:
        self._write(f"  {text}")

    def warn(self, text: str) -> None:
        print(f"  ! {text}", file=sys.stderr, flush=True)

    def error(self, text: str) -> None:
        print(f"  !! {text}", file=sys.stderr, flush=True)
