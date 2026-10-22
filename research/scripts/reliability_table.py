"""Reliability table for the calibration run: stated confidence vs observed agreement.

POST-HOC and descriptive. It was not pre-registered and is not used for pass/fail.
Usage:  uv run python research/scripts/reliability_table.py FORK_DIR [--out table.md]
Reads FORK_DIR/calibration-1/results.jsonl and FORK_DIR/issues.json (labelled set, run 1, answered rows).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

BINS = [(0.0, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 1.01)]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def truth_of(issue: dict) -> str | None:
    names = {l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])} & {"bug", "enhancement"}
    return next(iter(names)) if len(names) == 1 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fork_dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    fd = Path(a.fork_dir)
    issues = {int(i["number"]): i for i in json.loads((fd / "issues.json").read_text())}
    latest: dict[tuple, dict] = {}
    for line in (fd / "calibration-1" / "results.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            latest[(r["set"], r.get("run", 1), int(r["number"]))] = r
    rows = [r for k, r in latest.items() if k[0] == "labelled" and k[1] == 1
            and r["status"] == "succeeded" and r.get("confidence") is not None]
    out = ["# Reliability table (post-hoc, descriptive)", "",
           f"Answered rows with a confidence value: {len(rows)}.", "",
           "| stated confidence | n | mean stated | agreement with maintainers | 95% interval |",
           "|---|---|---|---|---|"]
    for lo, hi in BINS:
        g = [r for r in rows if lo <= r["confidence"] < hi]
        if not g:
            out.append(f"| {lo:.2f} to {min(hi, 1.0):.2f} | 0 | n/a | n/a | n/a |")
            continue
        k = sum(r["label"] == truth_of(issues[int(r["number"])]) for r in g)
        lo_i, hi_i = wilson(k, len(g))
        out.append(f"| {lo:.2f} to {min(hi, 1.0):.2f} | {len(g)} | {sum(r['confidence'] for r in g) / len(g):.3f} | "
                   f"{100 * k / len(g):.1f}% ({k}/{len(g)}) | {100 * lo_i:.0f} to {100 * hi_i:.0f}% |")
    hit = sum(r["label"] == truth_of(issues[int(r["number"])]) for r in rows)
    out += ["", f"Overall: mean stated confidence {sum(r['confidence'] for r in rows) / len(rows):.3f}, "
            f"agreement {hit / len(rows):.3f}."]
    text = "\n".join(out)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
