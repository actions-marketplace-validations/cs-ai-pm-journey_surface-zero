#!/usr/bin/env python3
"""Duplicate-precision eval 2, as frozen in prereg-dup-eval-2.md.

Offline: builds each test issue's candidate pool from issues.json, calls the
production Advisor with one of three arms, writes one JSON line per run.
Nothing is posted anywhere. Needs ANTHROPIC_API_KEY (the advisor's own key).

    uv run python research/scripts/dup_eval.py FORK_DIR --arms A,C --limit 5 --max-usd 2   # pilot
    uv run python research/scripts/dup_eval.py FORK_DIR --arms A,B,C --max-usd 14           # full run

Arms:
  A  production prompt, full pool                      (control)
  B  stricter duplicate rubric, full pool
  C  rubric B + top-20 TF-IDF prefilter of the pool

Resumable: (arm, issue) pairs already in results.jsonl are skipped, so the
pilot's rows count toward the full run. The draw is fixed by seed and written
to draw.json before any call.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

from surface_zero import advisor as adv
from surface_zero.issue_import import original_title
from surface_zero.labels import LabelSet

SEED = 20261012
N_DRAW = 60
OUTSIDE_OUTREACH = {1732}
DUP_PAIRS = {853: 298, 994: 931, 924: 912, 681: 413}  # child -> original
TOP_K_C = 20

RUBRIC_PARAGRAPH = (
    "Independently of which of those two actions you take, you may also flag this issue as a "
    "likely duplicate. A duplicate means that resolving the candidate issue would also resolve "
    "this one: the same underlying defect or the same requested change. Issues that are merely "
    "related, adjacent, in the same feature area, or that would be fixed by different changes "
    "are NOT duplicates; do not flag them. Only flag if the message below lists other issues "
    "and one of them meets this definition. If so, set duplicate_of to that issue's number and "
    "duplicate_rationale to one sentence explaining why resolving one resolves the other. When "
    "you are not sure it meets the definition, or no candidate issues are listed, leave "
    "duplicate_of unset.\n\n"
)

STOP = set(
    "the a an and or of to in on for is are was were be it this that with as at by from not "
    "but if when i my me we you your app can cant will would should could there have has had "
    "its do does did so no yes just also than then them they their into out up down".split()
)


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2 and t not in STOP]


def tfidf_top(query: dict, pool: list[dict], k: int) -> list[dict]:
    def doc(i):
        return tokens(i["title"]) * 2 + tokens(i["body"])

    docs = [doc(p) for p in pool]
    df = Counter()
    for d in docs:
        df.update(set(d))
    n = len(pool) + 1

    def vec(d):
        tf = Counter(d)
        return {t: (1 + math.log(c)) * math.log(n / (1 + df.get(t, 0)) + 1) for t, c in tf.items()}

    def norm(v):
        return math.sqrt(sum(x * x for x in v.values())) or 1.0

    q = vec(doc(query))
    qn = norm(q)
    scored = []
    for p, d in zip(pool, docs):
        v = vec(d)
        dot = sum(q.get(t, 0) * x for t, x in v.items())
        scored.append((dot / (qn * norm(v)), p["number"], p))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [s[2] for s in scored[:k]]


def load(fork_dir: Path):
    issues = json.loads((fork_dir / "issues.json").read_text())
    renames = json.loads((fork_dir / "rename_events.json").read_text())
    mapping = {int(k) for k in json.loads((fork_dir / "mapping-ForgeAS__medTimer-replay.json").read_text())}
    return issues, renames, mapping


def view(issue: dict, renames: dict, at: str) -> dict:
    """The issue as a candidate looked at time `at`: state at that time; title at
    filing where rename data exists (it only covers the 126 issues from replay 1,
    see the prereg amendment), otherwise the current title."""
    closed_then = issue["state"] == "closed" and issue.get("closed_at") and issue["closed_at"] <= at
    return {
        "number": issue["number"],
        "title": original_title(issue, renames.get(str(issue["number"]), [])),
        "body": issue.get("body") or "",
        "state": "closed" if closed_then else "open",
        "state_reason": issue.get("state_reason") if closed_then else None,
        "created_at": issue["created_at"],
    }


def draw(issues: list[dict], mapping: set[int]) -> list[int]:
    held = sorted(
        i["number"]
        for i in issues
        if i["number"] not in mapping
        and i.get("author_association") != "OWNER"
        and i["number"] not in OUTSIDE_OUTREACH
    )
    return random.Random(SEED).sample(held, N_DRAW)


def build_prompt(arm: str) -> str:
    base = adv.SYSTEM_PROMPT
    if arm == "A":
        return base
    head, rest = base.split("Independently of which", 1)
    _, tail = rest.split("Some candidate issues are marked", 1)
    return head + RUBRIC_PARAGRAPH + "Some candidate issues are marked" + tail


class Ctx:
    def __init__(self):
        self.calls = []

    def record_llm_call(self, **kw):
        self.calls.append(kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fork_dir")
    ap.add_argument("--arms", default="A,B,C")
    ap.add_argument("--limit", type=int, default=None, help="first N items of the fixed order (pilot)")
    ap.add_argument("--max-usd", type=float, required=True, help="hard stop on NEW spend this invocation")
    ap.add_argument("--labels", default="data/label_sets/medtimer-replay.json")
    ap.add_argument("--client", default="real", help=argparse.SUPPRESS)
    args = ap.parse_args()

    fd = Path(args.fork_dir)
    out_dir = fd / "dup-eval-2"
    out_dir.mkdir(exist_ok=True)
    issues, renames, mapping = load(fd)
    by_num = {i["number"]: i for i in issues}
    drawn = draw(issues, mapping)

    draw_path = out_dir / "draw.json"
    if draw_path.exists():
        if json.loads(draw_path.read_text())["drawn"] != drawn:
            sys.exit("draw.json differs from the seeded draw; refusing to continue")
    else:
        draw_path.write_text(json.dumps({"seed": SEED, "n": N_DRAW, "drawn": drawn,
                                         "known_pairs": DUP_PAIRS}, indent=2))
        print(f"wrote {draw_path}")

    items = [(n, None) for n in drawn] + [(c, p) for c, p in DUP_PAIRS.items()]
    if args.limit:
        items = items[: args.limit]

    results_path = out_dir / "results.jsonl"
    done, spent_before = set(), 0.0
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            r = json.loads(line)
            done.add((r["arm"], r["number"]))
            spent_before += r.get("cost_usd") or 0.0

    label_set = LabelSet.from_file(args.labels)
    if args.client == "real":
        advisor = adv.Advisor.from_env(label_set)
    else:  # test hook: module path "pkg.mod:factory"
        import importlib
        mod, fn = args.client.split(":")
        advisor = adv.Advisor(getattr(importlib.import_module(mod), fn)(), label_set)

    new_spend = 0.0
    arms = [a.strip() for a in args.arms.split(",")]
    with results_path.open("a") as out:
        for number, parent in items:
            issue = by_num[number]
            at = issue["created_at"]
            pool_all = [view(i, renames, at) for i in issues
                        if i["created_at"] < at and i["number"] != number]
            pool_all.sort(key=lambda c: c["created_at"])
            me = view(issue, renames, at)
            for arm in arms:
                if (arm, number) in done:
                    continue
                if new_spend >= args.max_usd:
                    print(f"STOP: reached --max-usd {args.max_usd} (new spend ${new_spend:.3f})")
                    return 2
                pool = tfidf_top(me, pool_all, TOP_K_C) if arm == "C" else pool_all
                payload = {"title": me["title"], "body": me["body"], "number": number,
                           "candidates": [{k: c[k] for k in ("number", "title", "body", "state", "state_reason")}
                                          for c in pool]}
                adv.SYSTEM_PROMPT = build_prompt(arm)
                ctx = Ctx()
                row = {"arm": arm, "number": number, "known_parent": parent,
                       "pool_size": len(pool), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                try:
                    res = advisor(payload, ctx)
                    r = res.response
                    row.update(status="abstained" if res.abstained else "succeeded",
                               duplicate_of=r.get("duplicate_of"),
                               duplicate_rationale=r.get("duplicate_rationale"),
                               label=r.get("suggested_label"))
                except Exception as exc:  # noqa: BLE001 - a failed call is evidence, record it
                    row.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
                finally:
                    adv.SYSTEM_PROMPT = build_prompt("A")
                row["input_tokens"] = sum(c.get("input_tokens") or 0 for c in ctx.calls)
                row["output_tokens"] = sum(c.get("output_tokens") or 0 for c in ctx.calls)
                row["cost_usd"] = sum(c.get("cost_usd") or 0.0 for c in ctx.calls)
                new_spend += row["cost_usd"]
                out.write(json.dumps(row) + "\n")
                out.flush()
                print(f"{arm} #{number}: {row['status']} dup={row.get('duplicate_of')} "
                      f"in={row['input_tokens']} ${row['cost_usd']:.4f} (new ${new_spend:.3f})")
    print(f"done. new spend ${new_spend:.3f}; total in file ${spent_before + new_spend:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
