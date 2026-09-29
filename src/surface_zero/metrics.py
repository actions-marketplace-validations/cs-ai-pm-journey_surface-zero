"""Exports the run ledger as newline-delimited JSON, the shape
scripts/publish-metrics.mjs in the portfolio repo expects.

Two fields that script's own comment lists ("overridden", "verified") are
deliberately left off every row here rather than filled in with a guess.
Neither is tracked anywhere in the ledger today: nothing in this codebase
records whether a maintainer corrected a suggestion, and nothing records a
human confirming a suggestion was actually right. Per ledger.py's own rule
("NULL means not measured, which is different from 0"), the honest move is
to omit the field, not report a false zero. The portfolio's aggregate script
already treats a missing value as "not counted" rather than crashing, so
this is compatible with what's already there, not a change to it. Filling
those in for real is a design question (how does a maintainer's after-the-
fact correction get captured at all) for a later block, not a metrics-export
problem.

Only terminal runs are exported. A `started` row with no finished_at isn't
a completed run yet, and doesn't belong in an operating-history export;
find_orphans in ledger.py is the right place to look for those, not here.

Added 2026-09-29, alongside `status`: the pre-existing `ok` boolean collapses
`abstained`, `ignored`, and `failed` into the same `false`, which B11W3's
metric-definitions work found made "runs" and "since" impossible to compute
honestly downstream (an ignored webhook delivery -- a non-issue event that
never reached the advisor at all -- was indistinguishable from a real,
processed abstain). `status` carries the ledger's actual RunStatus value so
a consumer can tell those apart; `ok` is left in place unchanged, since
existing consumers (and the ndjson shape's own docs) already depend on it.
"""

from __future__ import annotations

import json

from surface_zero.ledger import Ledger, RunStatus

# Surface Zero only ever runs at Gate A (comment-only, suggest but never
# apply). If a later block escalates past Gate A, this needs to read the
# actual rung reached per run instead of a constant, see ladder.json in the
# portfolio repo for what "rung" means there.
RUNG = "A"


def ledger_rows_as_ndjson(ledger: Ledger) -> str:
    """One NDJSON line per terminal run, oldest first."""
    lines = []
    for run in ledger.list_runs():
        if run.status == RunStatus.STARTED:
            continue  # not finished yet, see module docstring
        calls = ledger.llm_calls_for_run(run.run_id)
        cost = sum(c.cost_usd for c in calls if c.cost_usd is not None)
        row = {
            "ts": run.finished_at,
            "ok": run.status == RunStatus.SUCCEEDED,
            "status": run.status.value,
            "cost_usd": round(cost, 6),
            "rung": RUNG,
            "surface": f"github:{run.target}" if run.target else None,
        }
        lines.append(json.dumps(row, sort_keys=True))
    return "\n".join(lines) + ("\n" if lines else "")
