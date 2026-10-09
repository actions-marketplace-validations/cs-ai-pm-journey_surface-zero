"""Ledger: the durable record of every run, written before anything else happens.

Design rules (settled in the Block 11 design docs):

* Ledger-first. A run row exists in the ledger BEFORE the agent does any work,
  so a crash mid-run still leaves evidence. A row stuck in `started` is an
  orphan, and `find_orphans` surfaces it.
* Idempotent by construction. `idempotency_key` is UNIQUE. A second delivery of
  the same event does not create a second run, it returns the existing one.
  At-least-once delivery is the assumed default, so this is not optional.
* Terminal states are immutable. Once a run is succeeded, failed, abstained or
  suppressed, nothing rewrites it. Corrections are new runs, not edits.
* Token, cost and latency fields are deliberately nullable. NULL means "not
  measured", which is different from 0. A synthetic run has no tokens, and the
  ledger must not pretend otherwise.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = 1


class LedgerError(Exception):
    """Raised for ledger integrity violations (illegal transitions, unknown runs)."""


class RunStatus(StrEnum):
    """What the advisor itself did. Never conflated with what happened outbound,
    that's outbound_action's job, see OutboundAction below.

    Originally this also carried a SUPPRESSED value for "the kill switch blocked
    the comment", added on Monday before the kill switch existed. Removed once
    Wednesday's build made clear that was the wrong field for it: a run whose
    advisor logic succeeded but whose comment got suppressed is still, at the
    advisor level, a success. Two fields that can disagree about whether a run
    succeeded defeats the point of an unambiguous ledger. outbound_action alone
    now carries whether anything was actually posted.

    IGNORED is different from ABSTAINED, deliberately kept as its own value
    rather than folded into it. ABSTAINED means the advisor looked at the issue
    and declined to suggest a label. IGNORED means the advisor was never asked,
    because the event itself was out of scope, wrong event type, wrong issue
    action, a bot-authored issue. Collapsing those loses exactly the signal a
    later ops review needs: how much traffic never reached the advisor at all
    versus how much reached it and got no answer.
    """

    STARTED = "started"
    SUCCEEDED = "succeeded"
    ABSTAINED = "abstained"  # agent deliberately declined to advise
    IGNORED = "ignored"  # event was out of scope, the advisor was never called
    FAILED = "failed"


TERMINAL_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.ABSTAINED,
        RunStatus.IGNORED,
        RunStatus.FAILED,
    }
)


class OutboundAction(StrEnum):
    NONE = "none"
    COMMENT_POSTED = "comment_posted"
    COMMENT_SUPPRESSED = "comment_suppressed"
    # The agent ran and its recommendation is in the ledger, but the repo is in
    # shadow mode so no comment was ever attempted. Distinct from
    # COMMENT_SUPPRESSED (kill switch) on purpose: shadow is a planned state,
    # a kill switch activation is an intervention. Never count as "posted".
    SHADOW_RECORDED = "shadow_recorded"


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    idempotency_key: str
    event_type: str
    target: str | None
    status: RunStatus
    started_at: str
    finished_at: str | None
    latency_ms: float | None
    request: dict[str, Any] | None
    response: dict[str, Any] | None
    outbound_action: OutboundAction
    error_type: str | None
    error_message: str | None
    schema_version: int


class LlmCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    call_id: str
    run_id: str
    model: str | None
    started_at: str
    latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    error_type: str | None
    error_message: str | None


_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    idempotency_key  TEXT NOT NULL UNIQUE,
    event_type       TEXT NOT NULL,
    target           TEXT,
    status           TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    latency_ms       REAL,
    request_json     TEXT,
    response_json    TEXT,
    outbound_action  TEXT NOT NULL DEFAULT 'none',
    error_type       TEXT,
    error_message    TEXT,
    schema_version   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id        TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    model          TEXT,
    started_at     TEXT NOT NULL,
    latency_ms     REAL,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_usd       REAL,
    error_type     TEXT,
    error_message  TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls(run_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _dumps(obj: dict[str, Any] | None) -> str | None:
    return None if obj is None else json.dumps(obj, sort_keys=True, default=str)


def _loads(text: str | None) -> dict[str, Any] | None:
    return None if text is None else json.loads(text)


class Ledger:
    """SQLite-backed ledger. One short-lived connection per operation.

    Each operation commits before returning, so a record written here is
    durable even if the process dies on the next line.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn: sqlite3.Connection | None = None
        if self.path == ":memory:":
            # A shared connection is required, each new :memory: connection is a fresh DB.
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.row_factory = sqlite3.Row
        with closing(self._connect()) as conn:
            if self._memory_conn is None:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_DDL)

    # -- connection handling -------------------------------------------------

    def _connect(self) -> sqlite3.Connection | _Borrowed:
        if self._memory_conn is not None:
            return _Borrowed(self._memory_conn)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- writes --------------------------------------------------------------

    def begin_run(
        self,
        *,
        idempotency_key: str,
        event_type: str,
        target: str | None = None,
        request: dict[str, Any] | None = None,
    ) -> tuple[RunRecord, bool]:
        """Create a run in `started` state, or return the existing one.

        Returns (record, created). `created` is False when this idempotency key
        was already seen, which is how duplicate webhook deliveries are detected.
        The insert relies on the UNIQUE constraint, not a check-then-insert, so
        two concurrent deliveries cannot both win.
        """
        run_id = uuid.uuid4().hex
        with closing(self._connect()) as conn:
            cur = conn.execute(
                """
                INSERT INTO runs (run_id, idempotency_key, event_type, target, status,
                                  started_at, request_json, outbound_action, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    run_id,
                    idempotency_key,
                    event_type,
                    target,
                    RunStatus.STARTED.value,
                    _now(),
                    _dumps(request),
                    OutboundAction.NONE.value,
                    SCHEMA_VERSION,
                ),
            )
            created = cur.rowcount == 1
            conn.commit()
            row = conn.execute(
                "SELECT * FROM runs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return _run_from_row(row), created

    def finish_run(
        self,
        run_id: str,
        *,
        status: RunStatus,
        latency_ms: float | None = None,
        response: dict[str, Any] | None = None,
        outbound_action: OutboundAction = OutboundAction.NONE,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> RunRecord:
        """Move a run from `started` to a terminal state. Terminal is final."""
        if status not in TERMINAL_STATUSES:
            raise LedgerError(f"finish_run needs a terminal status, got {status!r}")
        with closing(self._connect()) as conn:
            cur = conn.execute(
                """
                UPDATE runs
                   SET status = ?, finished_at = ?, latency_ms = ?, response_json = ?,
                       outbound_action = ?, error_type = ?, error_message = ?
                 WHERE run_id = ? AND status = ?
                """,
                (
                    status.value,
                    _now(),
                    latency_ms,
                    _dumps(response),
                    outbound_action.value,
                    error_type,
                    error_message,
                    run_id,
                    RunStatus.STARTED.value,
                ),
            )
            conn.commit()
            if cur.rowcount != 1:
                existing = conn.execute(
                    "SELECT status FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if existing is None:
                    raise LedgerError(f"unknown run_id {run_id}")
                raise LedgerError(
                    f"run {run_id} is already {existing['status']}, terminal states are immutable"
                )
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return _run_from_row(row)

    def record_llm_call(
        self,
        run_id: str,
        *,
        model: str | None = None,
        latency_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> LlmCallRecord:
        """Record one LLM call against a run. Failed calls are recorded too."""
        call_id = uuid.uuid4().hex
        with closing(self._connect()) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO llm_calls (call_id, run_id, model, started_at, latency_ms,
                                           input_tokens, output_tokens, cost_usd,
                                           error_type, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        call_id,
                        run_id,
                        model,
                        _now(),
                        latency_ms,
                        input_tokens,
                        output_tokens,
                        cost_usd,
                        error_type,
                        error_message,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerError(f"cannot record LLM call for unknown run {run_id}") from exc
            row = conn.execute("SELECT * FROM llm_calls WHERE call_id = ?", (call_id,)).fetchone()
        return _call_from_row(row)

    # -- reads ---------------------------------------------------------------

    def get_run(self, run_id: str) -> RunRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else _run_from_row(row)

    def get_run_by_key(self, idempotency_key: str) -> RunRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return None if row is None else _run_from_row(row)

    def list_runs(self, status: RunStatus | None = None) -> list[RunRecord]:
        query = "SELECT * FROM runs"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status.value,)
        query += " ORDER BY started_at, rowid"
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [_run_from_row(r) for r in rows]

    def llm_calls_for_run(self, run_id: str) -> list[LlmCallRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY started_at, rowid", (run_id,)
            ).fetchall()
        return [_call_from_row(r) for r in rows]

    def find_orphans(self, older_than_seconds: float = 300.0) -> list[RunRecord]:
        """Runs still `started` after the cutoff. A crash between begin and finish leaves these."""
        cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_seconds)).isoformat(
            timespec="milliseconds"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE status = ? AND started_at <= ? ORDER BY started_at",
                (RunStatus.STARTED.value, cutoff),
            ).fetchall()
        return [_run_from_row(r) for r in rows]


class _Borrowed:
    """Wraps the shared in-memory connection so `closing()` and `with` don't close it."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def close(self) -> None:
        return None


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        idempotency_key=row["idempotency_key"],
        event_type=row["event_type"],
        target=row["target"],
        status=RunStatus(row["status"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        latency_ms=row["latency_ms"],
        request=_loads(row["request_json"]),
        response=_loads(row["response_json"]),
        outbound_action=OutboundAction(row["outbound_action"]),
        error_type=row["error_type"],
        error_message=row["error_message"],
        schema_version=row["schema_version"],
    )


def _call_from_row(row: sqlite3.Row) -> LlmCallRecord:
    return LlmCallRecord(
        call_id=row["call_id"],
        run_id=row["run_id"],
        model=row["model"],
        started_at=row["started_at"],
        latency_ms=row["latency_ms"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_usd"],
        error_type=row["error_type"],
        error_message=row["error_message"],
    )
