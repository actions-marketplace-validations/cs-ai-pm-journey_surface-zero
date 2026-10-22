"""Score the external replication against prereg-external-replication.md. Written and committed
BEFORE any results-arm-*.jsonl was read. It prints numbers next to the pre-registered criteria and
predictions; it does not decide anything for you.

Usage:  uv run python research/scripts/ext_score.py OUT_DIR [--out report.md]
Reads   OUT_DIR/manifest.json, results-arm-A.jsonl, results-arm-B.jsonl (B optional).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibrate_score import TAG, pct  # noqa: E402

THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
BINS = [(0.0, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 1.01)]
RECENT = "2025-06-01"


def load(path: Path) -> list[dict]:
    latest: dict[tuple, dict] = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            latest[(r["repo"], r["number"])] = r
    return list(latest.values())


def agree(r: dict) -> bool:
    return r["status"] == "succeeded" and r["label"] == r["truth"]


def core(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] != "failed"]
    ans = [r for r in ok if r["status"] == "succeeded"]
    hit = sum(1 for r in ans if r["label"] == r["truth"])
    base = Counter(r["truth"] for r in rows).most_common(1)
    return {"n": len(rows), "failed": len(rows) - len(ok), "answered": len(ans), "abstained": len(ok) - len(ans),
            "hit": hit, "acc": hit / len(ans) if ans else float("nan"),
            "abst_rate": (len(ok) - len(ans)) / len(ok) if ok else float("nan"),
            "baseline": (base[0][0], base[0][1] / len(rows)) if rows else ("n/a", float("nan")), "ans": ans, "ok": ok}


def line(name: str, c: dict) -> str:
    return (f"| {name} | {c['n']} | {c['answered']} | {c['abstained']} | {c['failed']} | "
            f"{pct(c['hit'], c['answered'])} | {pct(c['hit'], len(c['ok']))} | "
            f"{c['baseline'][0]} {100 * c['baseline'][1]:.0f}% |")


HEAD = ["| set | rows | answered | abstained | failed | agreement among answered | abstentions as misses | majority baseline |",
        "|---|---|---|---|---|---|---|---|"]


def threshold_block(rows: list[dict], label: str) -> list[str]:
    c = core(rows)
    ans = [r for r in c["ans"] if r.get("confidence") is not None]
    out = [f"### Threshold curve, {label} (answers with a confidence value: {len(ans)})", "",
           "| t | kept | share of answers kept | agreement among kept | lift over t=0 |", "|---|---|---|---|---|"]
    base = sum(agree(r) for r in ans) / len(ans) if ans else float("nan")
    fail_t1 = []
    for t in THRESHOLDS:
        kept = [r for r in ans if r["confidence"] >= t]
        if not kept:
            out.append(f"| {t:.2f} | 0 | 0% | n/a | n/a |")
            continue
        acc = sum(agree(r) for r in kept) / len(kept)
        lift = 100 * (acc - base)
        out.append(f"| {t:.2f} | {len(kept)} | {100 * len(kept) / len(ans):.0f}% | {100 * acc:.1f}% | {lift:+.1f} |")
        if len(kept) / len(ans) >= 0.5 and lift >= 3.0:
            fail_t1.append(t)
    out += ["", f"T1 (no threshold keeping at least 50% of answers lifts agreement by 3+ points): "
                f"{'HOLDS' if not fail_t1 else 'FAILS at ' + str(fail_t1)}", ""]
    return out


def reliability_block(rows: list[dict], label: str) -> list[str]:
    ans = [r for r in core(rows)["ans"] if r.get("confidence") is not None]
    out = [f"### Reliability table, {label}", "", "| stated confidence | n | mean stated | agreement |", "|---|---|---|---|"]
    acc = {}
    for lo, hi in BINS:
        g = [r for r in ans if lo <= r["confidence"] < hi]
        if g:
            k = sum(agree(r) for r in g)
            acc[(lo, hi)] = (k, len(g))
            out.append(f"| {lo:.2f} to {min(hi, 1.0):.2f} | {len(g)} | {sum(r['confidence'] for r in g) / len(g):.3f} | {pct(k, len(g))} |")
        else:
            out.append(f"| {lo:.2f} to {min(hi, 1.0):.2f} | 0 | n/a | n/a |")
    low, top = acc.get(BINS[0]), acc.get(BINS[-1])
    if low and top and low[1] >= 10:
        diff = 100 * (top[0] / top[1] - low[0] / low[1])
        out.append(f"\nT2 (low bin agreement at least 10 points below the 0.95+ bin): {'HOLDS' if diff >= 10 else 'FAILS'} "
                   f"(difference {diff:.1f} points)")
    else:
        out.append("\nT2: too few answers below 0.70 (fewer than 10) to read.")
    if ans:
        mc = sum(r["confidence"] for r in ans) / len(ans)
        ac = sum(agree(r) for r in ans) / len(ans)
        out.append(f"T3 (mean stated confidence below agreement): {'HOLDS' if mc < ac else 'FAILS'} "
                   f"(mean confidence {mc:.3f}, agreement {ac:.3f})")
    out.append("")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = Path(a.out_dir)
    manifest = json.loads((d / "manifest.json").read_text())
    rows_a = load(d / "results-arm-A.jsonl")
    shas = Counter(r.get("code_sha") for r in rows_a)
    primary = [r for r in manifest["repos"] if manifest["repos"][r]["group"] == "primary"]
    secondary = [r for r in manifest["repos"] if manifest["repos"][r]["group"] == "secondary"]
    out = ["# External replication scoring output (machine-generated; interpretation goes in the report)", "",
           f"Code under test (rows per commit): {dict(shas)}"]
    if len(shas) > 1:
        out.append("**WARNING: more than one commit produced these rows. Do not pool without saying so.**")
    out += ["", "## Arm A, primary repositories (stranger-filed issues)", ""] + HEAD
    prim_rows = [r for r in rows_a if r["repo"] in primary]
    per = {}
    for repo in primary:
        rr = [r for r in rows_a if r["repo"] == repo]
        per[repo] = core(rr) if rr else None
        if rr:
            out.append(line(repo, per[repo]))
    pooled = core(prim_rows) if prim_rows else None
    if pooled:
        out.append(line("POOLED primary", pooled))
        out += ["", "### Pre-registered criteria", "",
                f"- A1 pooled agreement among answered at least 90%: {'PASS' if pooled['acc'] >= 0.90 else 'FAIL'} "
                f"({100 * pooled['acc']:.1f}%; prediction 92%, 80% interval 87 to 96)"]
        for repo, c in per.items():
            if c is None:
                continue
            a2 = c["acc"] >= 0.85
            a3 = c["acc"] >= c["baseline"][1] + 0.10
            a4 = c["abst_rate"] <= 0.10
            out.append(f"- {repo}: A2 (at least 85%) {'PASS' if a2 else 'FAIL'}; A3 (baseline + 10 points) "
                       f"{'PASS' if a3 else 'FAIL'} (baseline {100 * c['baseline'][1]:.0f}%); A4 (abstention at most 10%) "
                       f"{'PASS' if a4 else 'FAIL'} ({100 * c['abst_rate']:.1f}%)")
        out += ["", "Predictions for reference: terminus 93, microeco 90, arocc 88; abstention 2 to 8% pooled.", ""]
        out += threshold_block(prim_rows, "pooled primary, arm A") + reliability_block(prim_rows, "pooled primary, arm A")
        rec = [r for r in prim_rows if r["created_at"] >= RECENT]
        old = [r for r in prim_rows if r["created_at"] < RECENT]
        out += ["### Recent (filed since 2025-06-01) against older, pooled primary (descriptive)", ""] + HEAD
        for nm, g in (("recent", rec), ("older", old)):
            if g:
                out.append(line(nm, core(g)))
        out.append("")
        buckets = [("empty (0)", 0, 0), ("short (1-199)", 1, 199), ("longer (200+)", 200, 10**9)]
        out += ["### By body length, pooled primary (descriptive)", ""] + HEAD
        for nm, lo, hi in buckets:
            g = [r for r in prim_rows if lo <= r.get("body_chars", 0) <= hi]
            if g:
                out.append(line(nm + (" (too small to read)" if len(g) < 20 else ""), core(g)))
        out.append("")
    sec_rows = [r for r in rows_a if r["repo"] in secondary]
    if sec_rows:
        out += ["## Arm A, secondary repositories (stranger-filed issues only)", ""] + HEAD
        for repo in secondary:
            rr = [r for r in sec_rows if r["repo"] == repo]
            if rr:
                out.append(line(repo, core(rr)))
        out.append(line("POOLED secondary", core(sec_rows)))
        dk = [r for r in sec_rows if r["repo"] == "DevEmperor/DictateKeyboard" and not TAG.search(r.get("title", ""))]
        if dk:
            out.append(line("DictateKeyboard excluding type-tagged titles", core(dk)))
        out += ["", "Prediction: secondary pooled agreement within 5 points of primary.", ""]
    path_b = d / "results-arm-B.jsonl"
    if path_b.exists():
        rows_b = load(path_b)
        out += ["## Arm B (bug, enhancement, documentation, question), primary repositories", ""] + HEAD
        bprim = [r for r in rows_b if r["repo"] in primary]
        if bprim:
            cb = core(bprim)
            out.append(line("POOLED primary, arm B", cb))
            chosen = Counter(r["label"] for r in cb["ans"])
            other = sum(v for k, v in chosen.items() if k not in ("bug", "enhancement"))
            out += ["", f"Labels chosen (answered): {dict(chosen)}",
                    f"Share choosing documentation or question: {pct(other, len(cb['ans']))}  [prediction 3 to 10%]"]
            ka = {(r["repo"], r["number"]): r for r in prim_rows}
            both = [(ka[(r["repo"], r["number"])], r) for r in bprim if (r["repo"], r["number"]) in ka]
            if both:
                a_hit = sum(agree(x) for x, _ in both)
                b_hit = sum(agree(y) for _, y in both)
                out.append(f"Same {len(both)} issues, strict agreement (abstentions and failures count as misses): arm A "
                           f"{100 * a_hit / len(both):.1f}%, arm B {100 * b_hit / len(both):.1f}% "
                           f"(prediction: B lower by 2 to 6 points)")
            out.append("")
    text = "\n".join(out)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
