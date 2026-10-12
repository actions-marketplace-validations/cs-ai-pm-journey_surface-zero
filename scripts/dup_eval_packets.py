#!/usr/bin/env python3
"""Build blind rating packets for dup-eval-2.

    python scripts/dup_eval_packets.py FORK_DIR

Reads dup-eval-2/results.jsonl (+ issues.json). Takes the distinct
(test issue, flagged issue) pairs flagged by ANY arm on the 60 drawn issues,
strips arm and rationale, shuffles with a fixed seed, and writes:

  dup-eval-2/rating-packet.md     what raters read (no arm, no model text)
  dup-eval-2/ratings-template.csv blank: pair_id,rating  (D / R / U)
  dup-eval-2/rating-key.json      pair_id -> issue, target, arms that flagged it
                                  (DO NOT OPEN before both raters have saved ratings)
The 4 known maintainer-labelled pairs are excluded: they have ground truth.
"""
import csv
import json
import random
import sys
from pathlib import Path

SEED = 20261013
BODY_LIMIT = 1400

fd = Path(sys.argv[1])
out = fd / "dup-eval-2"
issues = {i["number"]: i for i in json.loads((fd / "issues.json").read_text())}
draw = json.loads((out / "draw.json").read_text())["drawn"]
rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()]

pairs = {}
for r in rows:
    if r["number"] in draw and r.get("duplicate_of"):
        pairs.setdefault((r["number"], r["duplicate_of"]), set()).add(r["arm"])

order = sorted(pairs)
random.Random(SEED).shuffle(order)
key = {}
md = [
    "# Duplicate rating packet\n",
    "For each pair, decide whether the NEWER issue (A) is a duplicate of the OLDER issue (B).\n",
    "- **D** duplicate: resolving B would also resolve A (same underlying defect or the same requested change).\n"
    "- **R** related, not a duplicate: same feature area or theme, but a different fix or request.\n"
    "- **U** unrelated.\n",
    "Rate from the text only. Do not look anything up. Do not open rating-key.json. "
    "Write your answers in ratings-template.csv (a copy named with your name), one letter per pair.\n",
]


def clip(t):
    t = (t or "").strip()
    return t if len(t) <= BODY_LIMIT else t[:BODY_LIMIT].rstrip() + " [...]"


for n, (a, b) in enumerate(order, 1):
    pid = f"P{n:02d}"
    key[pid] = {"issue": a, "target": b, "arms": sorted(pairs[(a, b)])}
    ia, ib = issues[a], issues[b]
    md.append(f"\n---\n\n## {pid}\n")
    md.append(f"**A (newer): {ia['title']}**\n\n{clip(ia['body'])}\n")
    md.append(f"**B (older, {ib['state']}): {ib['title']}**\n\n{clip(ib['body'])}\n")

(out / "rating-packet.md").write_text("\n".join(md))
(out / "rating-key.json").write_text(json.dumps(key, indent=2))
with (out / "ratings-template.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["pair_id", "rating"])
    for pid in key:
        w.writerow([pid, ""])
print(f"{len(key)} pairs written")
