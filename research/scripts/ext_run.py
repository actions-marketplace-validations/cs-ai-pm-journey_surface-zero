"""Run the production advisor on the stranger-filed issues from ext_pull.py. Nothing is posted anywhere.

Usage:
  uv run python research/scripts/ext_run.py OUT_DIR --arm A --max-usd 3 [--repo OWNER/REPO] [--limit N]
  uv run python research/scripts/ext_run.py OUT_DIR --arm B --max-usd 3
Arm A offers bug and enhancement only; arm B offers bug, enhancement, documentation, question.
Writes OUT_DIR/results-arm-<A|B>.jsonl, one row per call, resumable (arm, repo, number).
Needs ANTHROPIC_API_KEY (in .env or exported). Threshold 0, labels only, no candidate issues.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from surface_zero import advisor as adv  # noqa: E402
from surface_zero.labels import LabelSet  # noqa: E402

LABEL_FILES = {"A": "data/label_sets/ext-eval-arm-a.json", "B": "data/label_sets/ext-eval-arm-b.json"}


class Ctx:
    def __init__(self):
        self.calls = []

    def record_llm_call(self, **kw):
        self.calls.append(kw)


def code_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def make_advisor(label_set: LabelSet, client_spec: str):
    if client_spec == "real":
        return adv.Advisor.from_env(label_set)
    import importlib  # test hook "pkg.mod:factory"

    mod, fn = client_spec.split(":")
    return adv.Advisor(getattr(importlib.import_module(mod), fn)(), label_set)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--arm", choices=["A", "B"], required=True)
    ap.add_argument("--max-usd", type=float, required=True, help="hard stop on NEW spend this invocation")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--limit", type=int, default=None, help="first N issues per repo (pilot)")
    ap.add_argument("--client", default="real", help=argparse.SUPPRESS)
    a = ap.parse_args()

    out = Path(a.out_dir)
    manifest = json.loads((out / "manifest.json").read_text())
    advisor = make_advisor(LabelSet.from_file(ROOT / LABEL_FILES[a.arm]), a.client)
    results = out / f"results-arm-{a.arm}.jsonl"
    done = set()
    if results.exists():
        for line in results.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["repo"], r["number"]))
    sha = code_sha()
    print(f"arm {a.arm}, code under test: {sha}")
    spend, n_new = 0.0, 0
    with results.open("a") as f:
        for repo, meta in manifest["repos"].items():
            if a.repo and repo != a.repo:
                continue
            issues = [i for i in json.loads((out / repo.replace("/", "__") / "issues.json").read_text())
                      if i["stranger"]]
            if a.limit:
                issues = issues[: a.limit]
            for i in issues:
                if (repo, i["number"]) in done:
                    continue
                if spend >= a.max_usd:
                    print(f"STOP: reached --max-usd {a.max_usd} (new spend ${spend:.3f}, {n_new} calls)")
                    return 2
                ctx = Ctx()
                row = {"repo": repo, "group": meta["group"], "arm": a.arm, "number": i["number"],
                       "truth": i["label"], "created_at": i["created_at"], "title": i["title"],
                       "code_sha": sha, "body_chars": len(i["body"].strip()), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                try:
                    res = advisor({"title": i["title"], "body": i["body"], "number": i["number"]}, ctx)
                    r = res.response
                    row.update(status="abstained" if res.abstained else "succeeded",
                               label=r.get("suggested_label"), confidence=r.get("confidence"),
                               reason=r.get("reason") if res.abstained else None)
                except Exception as exc:  # noqa: BLE001 - a failed call is evidence, record it
                    row.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
                row["input_tokens"] = sum(c.get("input_tokens") or 0 for c in ctx.calls)
                row["output_tokens"] = sum(c.get("output_tokens") or 0 for c in ctx.calls)
                row["cost_usd"] = sum(c.get("cost_usd") or 0.0 for c in ctx.calls)
                spend += row["cost_usd"]
                n_new += 1
                f.write(json.dumps(row) + "\n")
                f.flush()
                if n_new % 25 == 0 or n_new <= 3:
                    print(f"{repo}#{i['number']}: {row['status']} {row.get('label')} "
                          f"conf={row.get('confidence')} (call {n_new}, new ${spend:.3f})")
    print(f"done. {n_new} new calls, new spend ${spend:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
