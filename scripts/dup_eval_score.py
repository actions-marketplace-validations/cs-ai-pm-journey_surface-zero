#!/usr/bin/env python3
"""Score dup-eval-2 per prereg-dup-eval-2.md. Usage: python scripts/dup_eval_score.py FORK_DIR [--out FILE]

Needs dup-eval-2/{results.jsonl,draw.json,rating-key.json,ratings-claude.csv,ratings-adam.csv}.
Rating letters: D duplicate, R related, U unrelated, ? unrated (counts as not-D).
A flag is TRUE only if both raters say D (pre-registered). Lenient (either D) and
single-rater precisions are reported alongside, labelled as such.
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

fd = Path(sys.argv[1])
out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
d = fd / "dup-eval-2"
rows = [json.loads(l) for l in (d / "results.jsonl").read_text().splitlines()]
draw = json.loads((d / "draw.json").read_text())["drawn"]
key = json.loads((d / "rating-key.json").read_text())
issues = {i["number"]: i for i in json.loads((fd / "issues.json").read_text())}


def read(name):
    return {r["pair_id"]: (r["rating"] or "?").strip().upper() for r in csv.DictReader((d / name).open())}


C, A = read("ratings-claude.csv"), read("ratings-adam.csv")
pids = list(key)
isD = lambda r: r == "D"

both = {p for p in pids if isD(C[p]) and isD(A[p])}
either = {p for p in pids if isD(C[p]) or isD(A[p])}
rated = [p for p in pids if A[p] != "?" and C[p] != "?"]
bin_agree = sum(isD(C[p]) == isD(A[p]) for p in rated)
tri_agree = sum(C[p] == A[p] for p in rated)
pa, pb = sum(isD(C[p]) for p in rated) / len(rated), sum(isD(A[p]) for p in rated) / len(rated)
pe = pa * pb + (1 - pa) * (1 - pb)
kappa = (bin_agree / len(rated) - pe) / (1 - pe) if pe < 1 else float("nan")

by = {}
for r in rows:
    by.setdefault(r["arm"], {})[r["number"]] = r
pool = {n: by["A"][n]["pool_size"] for n in draw}  # arm A sends the full pool
big = [n for n in draw if pool[n] >= 100]

L = []
P = L.append
P("# Duplicate eval 2: results\n")
P(f"Pairs flagged by any arm: {len(pids)}. Rated by both: {len(rated)} (unrated: {[p for p in pids if A[p]=='?' or C[p]=='?']}).")
P(f"Both raters D: {len(both)}. Either rater D: {len(either)}. Split: {len(either)-len(both)}.")
P(f"Agreement on D vs not-D: {bin_agree}/{len(rated)} = {100*bin_agree/len(rated):.0f}%; on 3 classes: {tri_agree}/{len(rated)} = {100*tri_agree/len(rated):.0f}%; kappa (D vs not-D) {kappa:.2f}.\n")
P("| Arm | Flags (of 60) | Flag rate | Flag rate, pool>=100 | True (both D) | Precision strict | Precision lenient (either D) | Adam only | Claude only | Cost |")
P("|---|---|---|---|---|---|---|---|---|---|")
for arm in "ABC":
    flagged = {n: by[arm][n]["duplicate_of"] for n in draw if by[arm][n]["duplicate_of"]}
    pairs = [p for p in pids if (key[p]["issue"] in flagged and flagged[key[p]["issue"]] == key[p]["target"] and arm in key[p]["arms"])]
    nflag = len(pairs)
    fb = sum(1 for n in big if by[arm][n]["duplicate_of"])
    f = lambda S: f"{len([p for p in pairs if p in S])}/{nflag} = {100*len([p for p in pairs if p in S])/nflag:.0f}%" if nflag else "n/a"
    sa = {p for p in pids if isD(A[p])}
    sc = {p for p in pids if isD(C[p])}
    cost = sum(r["cost_usd"] for r in by[arm].values())
    P(f"| {arm} | {nflag} | {100*nflag/60:.0f}% | {fb}/{len(big)} = {100*fb/len(big):.0f}% | {len([p for p in pairs if p in both])} | {f(both)} | {f(either)} | {f(sa)} | {f(sc)} | ${cost:.2f} |")

P("\n## Known maintainer pairs (recall)\n")
for child, parent in {853: 298, 994: 931, 924: 912, 681: 413}.items():
    P(f"- #{child} expected #{parent}: " + ", ".join(f"{a}={by[a][child]['duplicate_of']}" for a in "ABC"))

P("\n## Pairs both raters called duplicate\n")
for p in sorted(both):
    k = key[p]
    P(f"- {p}: #{k['issue']} ({issues[k['issue']]['title']}) -> #{k['target']} ({issues[k['target']]['title']}); flagged by {','.join(k['arms'])}")
P("\n## Split pairs (one rater D)\n")
for p in sorted(either - both):
    k = key[p]
    P(f"- {p} Claude={C[p]} Adam={A[p]}: #{k['issue']} ({issues[k['issue']]['title']}) -> #{k['target']} ({issues[k['target']]['title']}); arms {','.join(k['arms'])}")
P(f"\nratings fingerprints: claude {hashlib.sha256((d/'ratings-claude.csv').read_bytes()).hexdigest()[:12]}, adam {hashlib.sha256((d/'ratings-adam.csv').read_bytes()).hexdigest()[:12]}")
text = "\n".join(L) + "\n"
print(text)
if out_path:
    Path(out_path).write_text(text)
