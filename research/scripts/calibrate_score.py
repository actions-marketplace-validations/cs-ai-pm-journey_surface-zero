"""Score the Friday calibration. Implements the frozen criteria 1-3 and Amendments 1-4
in tenant-configuration-spec.md. Written and committed BEFORE any results.jsonl was read.

Usage:  uv run python research/scripts/calibrate_score.py FORK_DIR [--out report.md]

Reads  FORK_DIR/calibration-1/results.jsonl, FORK_DIR/issues.json, FORK_DIR/replay-1-labels.json.
Strict scoring: an abstention is never a correct answer; both denominators are always shown.
Nothing here decides pass/fail for you; it prints the numbers next to the frozen predictions.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

# Amendment 3.2. The amendment said "18" titles; the pattern below was fixed before any data and
# the scorer prints the count it actually finds. It is a sensitivity check, not a headline.
TAG = re.compile(
    r"(feature|bug|enhancement|suggestion|improvement|request)\b[^\n]{0,15}(\]|\)|:)"
    r"|\[(feat|bug|enh|req|idea)|^\s*(feature|bug|request|enhancement|idea|suggestion)\b",
    re.I,
)
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
BUCKETS = [("empty (0)", 0, 0), ("short (1-199)", 1, 199), ("longer (200+)", 200, 10**9)]
MIN_GROUP = 20


def truth_of(issue: dict) -> str | None:
    names = {l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])} & {"bug", "enhancement"}
    return next(iter(names)) if len(names) == 1 else None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def pct(k: int, n: int) -> str:
    if n == 0:
        return "n/a (0 of 0)"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:.1f}% ({k} of {n}; 95% interval {100 * lo:.0f}-{100 * hi:.0f}%)"


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    latest: dict[tuple, dict] = {}
    for r in rows:  # resumable file: last row per (set, run, number) wins
        latest[(r["set"], r.get("run", 1), int(r["number"]))] = r
    return list(latest.values())


def summarize(rows: list[dict], truth: dict[int, str | None], title: str) -> list[str]:
    out = [f"### {title}"]
    n = len(rows)
    failed = [r for r in rows if r["status"] == "failed"]
    ok = [r for r in rows if r["status"] != "failed"]
    ans = [r for r in ok if r["status"] == "succeeded"]
    abst = [r for r in ok if r["status"] == "abstained"]
    hit = [r for r in ans if r["label"] == truth[int(r["number"])]]
    out.append(f"- rows: {n}; answered {len(ans)}; abstained {len(abst)}; failed (API/other errors) {len(failed)}")
    out.append(f"- abstention rate: {pct(len(abst), len(ok))} of non-failed")
    out.append(f"- accuracy among answered (comparable to the old 88%): {pct(len(hit), len(ans))}")
    out.append(f"- accuracy counting abstentions as misses: {pct(len(hit), len(ok))}")
    base = Counter(truth[int(r["number"])] for r in rows).most_common(1)
    if base:
        out.append(f"- always-'{base[0][0]}' baseline on this set: {pct(base[0][1], n)}")
    out.append("")
    out.append("Threshold curve (rows the model answered, kept if confidence >= t; abstentions stay out):")
    out.append("")
    out.append("| t | kept | coverage of non-failed | accuracy among kept |")
    out.append("|---|---|---|---|")
    for t in [0.0] + THRESHOLDS:
        kept = [r for r in ans if r.get("confidence") is not None and r["confidence"] >= t]
        k_hit = sum(1 for r in kept if r["label"] == truth[int(r["number"])])
        out.append(f"| {t:.2f} | {len(kept)} | {100 * len(kept) / max(len(ok), 1):.0f}% | "
                   f"{100 * k_hit / len(kept):.1f}% |" if kept else f"| {t:.2f} | 0 | 0% | n/a |")
    nc = sum(1 for r in ans if r.get("confidence") is None)
    if nc:
        out.append(f"\n{nc} answered rows had no usable confidence (dropped at every t > 0).")
    out.append("")
    return out


def body_breakdown(rows: list[dict], truth: dict[int, str | None]) -> list[str]:
    out = ["### Amendment 4: by body length (descriptive only, no pass/fail use)", "",
           "| body | n | abstained | answered | accuracy among answered |", "|---|---|---|---|---|"]
    ok = [r for r in rows if r["status"] != "failed"]
    for name, lo, hi in BUCKETS:
        g = [r for r in ok if lo <= r.get("body_chars", 0) <= hi]
        a = [r for r in g if r["status"] == "succeeded"]
        h = sum(1 for r in a if r["label"] == truth[int(r["number"])])
        flag = "  (too small to read)" if len(g) < MIN_GROUP else ""
        out.append(f"| {name} | {len(g)}{flag} | {pct(len(g) - len(a), len(g))} | {len(a)} | {pct(h, len(a))} |")
    out.append("")
    return out


def sig(r: dict | None) -> tuple:
    """(status, label) with abstention label dropped; missing/failed rows are None."""
    if r is None or r["status"] == "failed":
        return ("missing",)
    return ("abstained",) if r["status"] == "abstained" else ("succeeded", r["label"])


def paired(rows: list[dict], replay: dict, truth: dict[int, str | None]) -> list[str]:
    out = ["## Paired comparison on the 69 replay-1 issues (Amendments 2 and 3)", ""]
    runs = {k: {int(r["number"]): r for r in rows if r["set"] == "paired69" and r.get("run", 1) == k} for k in (1, 2)}
    nums = sorted(int(n) for n in replay)
    old = {n: ("abstained",) if replay[str(n)]["status"] == "abstained" else ("succeeded", replay[str(n)]["label"])
           for n in nums}
    lab66 = [n for n in nums if truth.get(n)]
    out.append(f"Issues: {len(nums)} (labelled with bug/enhancement: {len(lab66)}). Rows found: run 1 {len(runs[1])}, run 2 {len(runs[2])}.")
    out.append("")
    # replay-1 own accuracy, both denominators
    o_ans = [n for n in nums if old[n][0] == "succeeded"]
    o_hit = [n for n in o_ans if old[n][1] == truth.get(n)]
    o_ans_l = [n for n in o_ans if truth.get(n)]
    out.append(f"Replay 1 (old schema, candidates on): {len(o_hit)} of {len(o_ans)} answered match (old definition); "
               f"{len(o_hit)} of {len(o_ans_l)} among labelled-and-answered.")
    out.append("")
    for k in (1, 2):
        if not runs[k]:
            continue
        r = runs[k]
        same = [n for n in nums if n in r and sig(r[n]) == old[n]]
        ans = [n for n in nums if n in r and r[n]["status"] == "succeeded"]
        ans_l = [n for n in ans if truth.get(n)]
        hit = [n for n in ans_l if r[n]["label"] == truth[n]]
        both = [n for n in nums if n in r and old[n][0] == "succeeded" and r[n]["status"] == "succeeded"]
        agree = [n for n in both if old[n][1] == r[n]["label"]]
        flip = [n for n in nums if n in r and (old[n][0] == "abstained") != (r[n]["status"] == "abstained")]
        out.append(f"New run {k}: same status and same label as replay 1: {pct(len(same), len(nums))}  "
                   f"[frozen prediction: at least 90%]")
        out.append(f"  - both answered: agree {len(agree)}, disagree {len(both) - len(agree)}; "
                   f"answered/abstained changed: {len(flip)}")
        out.append(f"  - accuracy vs weak truth: {len([n for n in ans_l if r[n]['label'] == truth[n]])} of {len(ans_l)} "
                   f"labelled-and-answered; {len(hit)} of {len(ans)} answered (old denominator style)")
        out.append("")
    if runs[1] and runs[2]:
        both = [n for n in nums if n in runs[1] and n in runs[2]]
        k = sum(1 for n in both if sig(runs[1][n]) != sig(runs[2][n]))
        m1 = sum(1 for n in nums if n in runs[1] and sig(runs[1][n]) != old[n])
        m2 = sum(1 for n in nums if n in runs[2] and sig(runs[2][n]) != old[n])
        out.append("Noise floor and reading rule (Amendment 2.6):")
        out.append(f"- k = run 1 vs run 2 disagree on {k} of {len(both)} issues (noise floor)")
        out.append(f"- m = new run 1 differs from replay 1 on {m1}; new run 2 differs on {m2}")
        out.append("- Claim a schema effect only if m clearly exceeds k. If m is within a few issues of k, the schema "
                   "effect is not distinguishable from randomness at n = 69. Decide which, after reading these numbers, "
                   "in the report; do not change the rule.")
        out.append("")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fork_dir")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    fd = Path(a.fork_dir)
    rows = load_rows(fd / "calibration-1" / "results.jsonl")
    issues = {int(i["number"]): i for i in json.loads((fd / "issues.json").read_text())}
    replay = json.loads((fd / "replay-1-labels.json").read_text())
    truth = {n: truth_of(i) for n, i in issues.items()}
    shas = Counter(r.get("code_sha") for r in rows)

    out = ["# Calibration scoring output (machine-generated; interpretation goes in the Calibration Report)", ""]
    out.append(f"Code under test (rows per commit): {dict(shas)}")
    if len(shas) > 1:
        out.append("**WARNING: more than one commit produced these rows. Do not pool them without saying so.**")
    out.append("")
    lab = [r for r in rows if r["set"] == "labelled" and r.get("run", 1) == 1]
    out.append("## Labelled MedTimer set (criterion 1, unpaired view; Amendment 3 denominator corrections)")
    out.append("")
    out.append("Frozen figures for comparison: old 88% (59/67 answered; 59/66 labelled-and-answered 89%); "
               "always-enhancement baseline on this set 58% (255 of 440).")
    out.append("")
    if lab:
        out += summarize(lab, truth, f"All {len(lab)} labelled rows (headline)")
        tagged = [r for r in lab if TAG.search(issues[int(r["number"])]["title"])]
        out.append(f"Sensitivity (Amendment 3.2): titles matching the tag pattern: {len(tagged)} "
                   f"(the amendment said 18; the pattern was fixed before data and is reported as found).")
        out.append("")
        out += summarize([r for r in lab if r not in tagged], truth, "Excluding type-tagged titles")
        out += body_breakdown(lab, truth)
    else:
        out.append("No labelled rows yet.\n")
    out += paired(rows, replay, truth)
    text = "\n".join(out)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
