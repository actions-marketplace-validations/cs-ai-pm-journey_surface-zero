#!/usr/bin/env python3
"""Friday calibration run: the production Advisor (current schema, threshold 0, labels only,
no candidate issues) on MedTimer issues. Frozen design: tenant-configuration-spec.md in the
block-12 vault folder (Friday criteria, Amendments 1 and 2). Run from the repository root.

    # 1. the 440 labelled issues (one run)
    uv run python research/scripts/calibrate.py FORK_DIR --set labelled --max-usd 3.5
    # 2. the 69 replay-1 issues, twice, as separate runs (paired comparison + noise floor)
    uv run python research/scripts/calibrate.py FORK_DIR --set paired69 --run 1 --max-usd 1
    uv run python research/scripts/calibrate.py FORK_DIR --set paired69 --run 2 --max-usd 1

Writes FORK_DIR/calibration-1/results.jsonl, one row per call, resumable: (set, run, issue)
rows already present are skipped, so an interrupted run continues where it stopped.
Nothing is posted anywhere. Needs ANTHROPIC_API_KEY (in .env or exported).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dup_eval as de  # noqa: E402
from surface_zero import advisor as adv  # noqa: E402
from surface_zero.labels import LabelSet  # noqa: E402

SEEDED = 57  # fork numbers 1..57 were seeded; 58 and up were replayed (replay 1 scored 69)
EXPECTED = {"labelled": 440, "paired69": 69}  # frozen data facts; the script refuses to run if they change


def code_sha() -> str | None:
    """Commit of the code under test, so every number is tied to what produced it."""
    try:
        root = Path(__file__).resolve().parent.parent.parent
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001 - provenance is best effort, the run must not die for it
        return None


def truth_of(issue: dict) -> str | None:
    names = {l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])} & {"bug", "enhancement"}
    return next(iter(names)) if len(names) == 1 else None


def build_sets(fd: Path, issues: list[dict]) -> dict[str, list[int]]:
    by_num = {i["number"]: i for i in issues}
    mapping = json.loads((fd / "mapping-ForgeAS__medTimer-replay.json").read_text())
    return {
        "labelled": sorted(n for n, i in by_num.items() if truth_of(i)),
        "paired69": sorted(int(o) for o, f in mapping.items() if f > SEEDED),
    }


class Ctx:
    def __init__(self):
        self.calls = []

    def record_llm_call(self, **kw):
        self.calls.append(kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fork_dir")
    ap.add_argument("--set", choices=["labelled", "paired69"], required=True)
    ap.add_argument("--run", type=int, default=1, help="run number within the set (paired69 is run twice: 1 and 2)")
    ap.add_argument("--limit", type=int, default=None, help="first N issues only (pilot)")
    ap.add_argument("--max-usd", type=float, required=True, help="hard stop on NEW spend this invocation")
    ap.add_argument("--labels", default="data/label_sets/medtimer-replay.json")
    ap.add_argument("--client", default="real", help=argparse.SUPPRESS)
    args = ap.parse_args()

    fd = Path(args.fork_dir)
    issues, renames, _ = de.load(fd)
    by_num = {i["number"]: i for i in issues}
    sets = build_sets(fd, issues)
    for name, want in EXPECTED.items():
        if len(sets[name]) != want:
            sys.exit(f"set {name} has {len(sets[name])} issues, frozen expectation is {want}; refusing to run")
    numbers = sets[args.set][: args.limit] if args.limit else sets[args.set]

    out_dir = fd / "calibration-1"
    out_dir.mkdir(exist_ok=True)
    results = out_dir / "results.jsonl"
    done = set()
    if results.exists():
        for line in results.read_text().splitlines():
            r = json.loads(line)
            done.add((r["set"], r["run"], r["number"]))

    label_set = LabelSet.from_file(args.labels)
    if args.client == "real":
        advisor = adv.Advisor.from_env(label_set)
    else:  # test hook "pkg.mod:factory"
        import importlib

        mod, fn = args.client.split(":")
        advisor = adv.Advisor(getattr(importlib.import_module(mod), fn)(), label_set)

    sha = code_sha()
    print(f"code under test: {sha}")
    new_spend, n_new = 0.0, 0
    with results.open("a") as out:
        for n in numbers:
            if (args.set, args.run, n) in done:
                continue
            if new_spend >= args.max_usd:
                print(f"STOP: reached --max-usd {args.max_usd} (new spend ${new_spend:.3f}, {n_new} calls)")
                return 2
            issue = by_num[n]
            v = de.view(issue, renames, issue["created_at"])
            payload = {"title": v["title"], "body": v["body"], "number": n}
            ctx = Ctx()
            row = {"set": args.set, "run": args.run, "number": n, "truth": truth_of(issue),
                   "code_sha": sha, "body_chars": len((issue.get("body") or "").strip()),
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            try:
                res = advisor(payload, ctx)
                r = res.response
                row.update(status="abstained" if res.abstained else "succeeded",
                           label=r.get("suggested_label"), confidence=r.get("confidence"),
                           reason=r.get("reason") if res.abstained else None)
            except Exception as exc:  # noqa: BLE001 - a failed call is evidence, record it
                row.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
            row["input_tokens"] = sum(c.get("input_tokens") or 0 for c in ctx.calls)
            row["output_tokens"] = sum(c.get("output_tokens") or 0 for c in ctx.calls)
            row["cost_usd"] = sum(c.get("cost_usd") or 0.0 for c in ctx.calls)
            new_spend += row["cost_usd"]
            n_new += 1
            out.write(json.dumps(row) + "\n")
            out.flush()
            if n_new % 20 == 0 or n_new <= 3:
                print(f"#{n}: {row['status']} {row.get('label')} conf={row.get('confidence')} "
                      f"(call {n_new}, new ${new_spend:.3f})")
    print(f"done. {n_new} new calls, new spend ${new_spend:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
