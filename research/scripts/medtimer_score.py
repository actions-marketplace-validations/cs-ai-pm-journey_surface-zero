#!/usr/bin/env python3
"""Score the MedTimer fork replay against prereg-fork-replay-1.md.

Usage:
    python research/scripts/medtimer_score.py LEDGER_DB FORK_DIR [--out RESULT.md]

LEDGER_DB  a local copy of /data/ledger.db (read only here).
FORK_DIR   the medtimer-fork vault folder (issues.json, mapping-*.json).

Rules are the pre-registered ones. Nothing here is tuned after seeing results.
Read-only: opens the ledger with mode=ro and never writes to GitHub.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

FORK = "ForgeAS/medTimer-replay"
SEED_COUNT = 57  # fork numbers 1..57 are seeds; 58..61 were discarded canaries
CORRECTIONS = (544, 793, 821, 853, 1209, 1297)
DUP_PAIRS = {853: 298, 994: 931, 924: 912, 681: 413}  # child -> original
PREDICTIONS = {
    "corrections_enhancement": "3 of 6 (2-5; amended 1-5)",
    "dup_pairs": "2 of 4 (1-3)",
    "false_dup_flags": "4 (0-9)",
    "label_match": "85% (75-92%)",
    "abstain": "8% (2-18%)",
    "disagreements": "8 (4-14)",
    "failed": "0 (0-2)",
    "cost": "$0.45 ($0.25-0.80)",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger")
    ap.add_argument("fork_dir")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fd = Path(args.fork_dir)
    issues = {i["number"]: i for i in json.loads((fd / "issues.json").read_text())}
    mapping = json.loads((fd / "mapping-ForgeAS__medTimer-replay.json").read_text())
    orig_to_fork = {int(k): int(v) for k, v in mapping.items()}
    fork_to_orig = {v: k for k, v in orig_to_fork.items()}

    con = sqlite3.connect(f"file:{args.ledger}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    runs = con.execute(
        "SELECT * FROM runs WHERE target = ? AND event_type = 'issues.opened' "
        "ORDER BY started_at",
        (FORK,),
    ).fetchall()
    costs = {
        r["run_id"]: r["c"]
        for r in con.execute("SELECT run_id, SUM(cost_usd) AS c FROM llm_calls GROUP BY run_id")
    }

    scored: dict[int, dict] = {}  # original number -> row
    discarded = []
    for r in runs:
        req = json.loads(r["request_json"] or "{}")
        payload = req.get("payload", req)
        fork_n = payload.get("number")
        if fork_n is None:
            discarded.append((None, r["status"], "no issue number"))
            continue
        if fork_n <= SEED_COUNT or fork_n not in fork_to_orig:
            discarded.append((fork_n, r["status"], "not in mapping (canary leftover)"))
            continue
        resp = json.loads(r["response_json"] or "{}")
        orig = fork_to_orig[fork_n]
        if orig in scored:  # keep the latest run per issue, flag the repeat
            scored[orig]["repeat"] = True
        scored[orig] = {
            "orig": orig,
            "fork": fork_n,
            "status": r["status"],
            "outbound": r["outbound_action"],
            "label": resp.get("suggested_label"),
            "dup_fork": resp.get("duplicate_of"),
            "latency_ms": r["latency_ms"],
            "cost": costs.get(r["run_id"], 0.0) or 0.0,
            "error": r["error_type"],
            "repeat": scored.get(orig, {}).get("repeat", False),
        }

    # Which replay issues should exist? Everything in the mapping above the seeds.
    replayed = {o for o, f in orig_to_fork.items() if f > SEED_COUNT}
    missing = sorted(replayed - set(scored))

    rows = [scored[o] for o in sorted(scored)]
    ok = [r for r in rows if r["status"] == "succeeded"]
    abst = [r for r in rows if r["status"] == "abstained"]
    failed = [r for r in rows if r["status"] == "failed"]
    other = [r for r in rows if r["status"] not in ("succeeded", "abstained", "failed")]

    def cur(o):
        return set(issues[o]["labels"]) if o in issues else set()

    labeled = [r for r in ok if r["label"]]
    match = [r for r in labeled if r["label"] in cur(r["orig"])]
    disagree = [r for r in labeled if r["label"] not in cur(r["orig"])]

    corr = [scored[o] for o in CORRECTIONS if o in scored]
    corr_hit = [r for r in corr if r["label"] == "enhancement"]

    pair_rows = []
    for child, parent in DUP_PAIRS.items():
        r = scored.get(child)
        flagged = fork_to_orig.get(r["dup_fork"]) if r and r["dup_fork"] else None
        pair_rows.append((child, parent, flagged, r))
    pair_hit = [p for p in pair_rows if p[2] == p[1]]

    child_set = set(DUP_PAIRS)
    other_flags = [r for r in rows if r["dup_fork"] and r["orig"] not in child_set]

    n_total = len(rows)
    n_run = len(ok) + len(abst)
    cost = sum(r["cost"] for r in rows)
    lat = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
    p50 = lat[len(lat) // 2] if lat else 0
    p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0

    def title(o):
        return issues[o]["title"] if o in issues else "?"

    L = []
    A = L.append
    A("# MedTimer fork replay 1: results\n")
    A(f"Scored {n_total} replayed issues (expected {len(replayed)}); "
      f"{len(ok)} succeeded, {len(abst)} abstained, {len(failed)} failed, {len(other)} other.")
    if missing:
        A(f"\nWARNING: no ledger row for original issues {missing}. Replay incomplete or rows missing.")
    if any(r["repeat"] for r in rows):
        A("\nWARNING: more than one run for: " + ", ".join(str(r["orig"]) for r in rows if r["repeat"])
          + " (latest used).")
    A(f"\nDiscarded test artifacts (not scored): {len(discarded)} ledger rows "
      f"{[(n, s) for n, s, _ in discarded]}.\n")

    A("## Prediction vs result\n")
    A("| # | Measure | Predicted | Result |")
    A("|---|---|---|---|")
    A(f"| 1 | Corrections labeled enhancement | {PREDICTIONS['corrections_enhancement']} | "
      f"{len(corr_hit)} of {len(corr)} |")
    A(f"| 2 | Known duplicate pairs named correctly (strict) | {PREDICTIONS['dup_pairs']} | "
      f"{len(pair_hit)} of {len(pair_rows)} |")
    A(f"| 3 | Duplicate flags on non-pair issues (upper bound on false; unadjudicated) | "
      f"{PREDICTIONS['false_dup_flags']} | {len(other_flags)} of {n_total - len(child_set & set(scored))} |")
    pct = 100 * len(match) / len(labeled) if labeled else float("nan")
    A(f"| 4 | Label match (non-abstained) | {PREDICTIONS['label_match']} | "
      f"{len(match)} of {len(labeled)} = {pct:.0f}% |")
    ab = 100 * len(abst) / n_run if n_run else float("nan")
    A(f"| 5 | Abstain rate | {PREDICTIONS['abstain']} | {len(abst)} of {n_run} = {ab:.0f}% |")
    A(f"| 6 | Disagreements to adjudicate | {PREDICTIONS['disagreements']} | {len(disagree)} |")
    A(f"| 7 | Failed runs | {PREDICTIONS['failed']} | {len(failed)} |")
    A(f"| 8 | Total cost | {PREDICTIONS['cost']} | ${cost:.3f} |")
    A(f"\nLatency p50 {p50 / 1000:.1f}s, p95 {p95 / 1000:.1f}s.\n")

    A("## Corrections (target: enhancement)\n")
    for o in CORRECTIONS:
        r = scored.get(o)
        note = " (easy case: reporter wrote 'improvement'; not informative)" if o == 793 else ""
        A(f"- #{o} ({title(o)}): bot said {r['label'] if r else 'NO RUN'}"
          f"{' [' + r['status'] + ']' if r and r['status'] != 'succeeded' else ''}{note}")

    A("\n## Duplicate pairs (strict: must name the maintainer's original)\n")
    for child, parent, flagged, r in pair_rows:
        if r is None:
            res = "NO RUN"
        elif flagged is None:
            res = "no flag"
        elif flagged == parent:
            res = "CORRECT"
        else:
            res = f"flagged #{flagged} ({title(flagged)}), not #{parent}: defensible alternate? adjudicate"
        A(f"- #{child} ({title(child)}) -> expected #{parent} ({title(parent)}): {res}")

    A("\n## Duplicate flags on other issues (adjudicate each; not all are false)\n")
    for r in other_flags:
        fo = fork_to_orig.get(r["dup_fork"])
        A(f"- #{r['orig']} ({title(r['orig'])}) flagged #{fo} ({title(fo) if fo else '?'})")
    if not other_flags:
        A("- none")

    A("\n## Disagreements (bot label differs from current label on the original repo)\n")
    for r in disagree:
        A(f"- #{r['orig']} ({title(r['orig'])}): bot {r['label']}, current {sorted(cur(r['orig'])) or 'none'}")
    if not disagree:
        A("- none")

    bug_for_feature = [r for r in disagree if r["label"] == "bug"]
    A(f"\nDisagreements where the bot said bug: {len(bug_for_feature)} of {len(disagree)} "
      "(prediction 9: most common error is calling a feature request a bug).")

    if abst or failed or other:
        A("\n## Abstained / failed / other\n")
        for r in abst + failed + other:
            A(f"- #{r['orig']} ({title(r['orig'])}): {r['status']} {r['error'] or ''}")

    out = "\n".join(L) + "\n"
    print(out)
    if args.out:
        Path(args.out).write_text(out)
        print(f"written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
