"""Independently re-check the numbers someone else (Devin) reported for a candidate evaluation repo.

Read-only: only GET requests through the logged-in `gh` CLI. Posts, stars and contacts nothing.
Written separately from Devin's scripts on purpose, so a shared bug cannot hide in both.

Usage:  uv run python research/scripts/verify_eval_repo.py OWNER/REPO [OWNER/REPO ...] [--out verify.md] [--seed 1]
For each repo it prints:
  - exactly-bug / exactly-enhancement / both counts (issues only, open and closed)
  - share of those issues whose title matches the scorer's type-tag pattern (calibrate_score.TAG)
  - distinct authors, bot authors, and the share filed by the single most frequent author
  - who applied the bug/enhancement label on a seeded random sample (first 'labeled' event):
        by a bot or app / by the author within 10 s of filing / later by someone else / later by the author
  - whether any issue template sets a non-empty `labels:` field today
"""
from __future__ import annotations

import argparse
import base64
import json
import random
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from calibrate_score import TAG  # noqa: E402  same pattern the scorer uses

BOT_PREFIXES = ("dependabot", "renovate", "github-actions", "allcontributors", "imgbot", "snyk-bot",
                "pre-commit-ci", "codecov", "stale")


def gh(path: str, *params: str):
    cmd = ["gh", "api", "-X", "GET", path]
    for p in params:
        cmd += ["-f", p]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def paged(path: str, *params: str) -> list:
    out: list = []
    page = 1
    while True:
        chunk = gh(path, *params, "per_page=100", f"page={page}")
        if not chunk:
            break
        out += chunk
        if len(chunk) < 100:
            break
        page += 1
    return out


def is_bot(login: str, utype: str | None) -> bool:
    low = (login or "").lower()
    return utype == "Bot" or low.endswith("[bot]") or low.startswith(BOT_PREFIXES)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def template_labels(repo: str) -> list[str]:
    listing = gh(f"repos/{repo}/contents/.github/ISSUE_TEMPLATE")
    found = []
    for f in listing or []:
        if not f["name"].lower().endswith((".md", ".yml", ".yaml")) or f["name"].startswith("config"):
            continue
        meta = gh(f"repos/{repo}/contents/{f['path']}")
        if not meta or "content" not in meta:
            continue
        text = base64.b64decode(meta["content"]).decode("utf-8", "replace")
        m = re.search(r"^labels\s*:\s*(.*)$", text, re.M)
        if m and m.group(1).strip() not in ("", "[]", "''", '""'):
            found.append(f"{f['name']}: {m.group(1).strip()}")
        elif m and m.group(1).strip() == "":  # yaml list form on following lines
            tail = text[m.end():].splitlines()[:3]
            if any(t.strip().startswith("-") for t in tail):
                found.append(f"{f['name']}: (list)")
    return found


def audit(repo: str, issues: dict[int, dict], labels: dict[int, str], seed: int, per_class: int = 10) -> Counter:
    rnd = random.Random(seed)
    sample: list[int] = []
    for cls in ("bug", "enhancement"):
        nums = sorted(n for n, l in labels.items() if l == cls)
        sample += rnd.sample(nums, min(per_class, len(nums)))
    c: Counter = Counter()
    for n in sample:
        evs = paged(f"repos/{repo}/issues/{n}/events")
        ev = next((e for e in evs if e.get("event") == "labeled" and e["label"]["name"] == labels[n]), None)
        if ev is None:
            c["no labeled event (unknown)"] += 1
            continue
        actor = (ev.get("actor") or {}).get("login", "")
        if is_bot(actor, (ev.get("actor") or {}).get("type")) or ev.get("performed_via_github_app"):
            c["bot or app"] += 1
            continue
        author = issues[n]["user"]["login"]
        delay = (ts(ev["created_at"]) - ts(issues[n]["created_at"])).total_seconds()
        if actor == author:
            c["author, within 10 s of filing" if delay < 10 else "author, later"] += 1
        else:
            c["someone else, later" if delay >= 10 else "someone else, within 10 s"] += 1
    c["sample size"] = len(sample)
    return c


def check(repo: str, seed: int) -> list[str]:
    by_label: dict[str, dict[int, dict]] = {}
    for lab in ("bug", "enhancement"):
        rows = paged(f"repos/{repo}/issues", f"labels={lab}", "state=all")
        by_label[lab] = {i["number"]: i for i in rows if "pull_request" not in i}
    b, e = by_label["bug"], by_label["enhancement"]
    both = set(b) & set(e)
    issues = {n: i for n, i in {**b, **e}.items() if n not in both}
    labels = {n: ("bug" if n in b else "enhancement") for n in issues}
    nb, ne = sum(1 for l in labels.values() if l == "bug"), sum(1 for l in labels.values() if l == "enhancement")
    tagged = sum(1 for i in issues.values() if TAG.search(i["title"]))
    authors = Counter(i["user"]["login"] for i in issues.values())
    bots = sum(1 for i in issues.values() if is_bot(i["user"]["login"], i["user"].get("type")))
    top, topn = authors.most_common(1)[0] if authors else ("n/a", 0)
    since = sum(1 for i in issues.values() if i["created_at"] >= "2025-06-01")
    total = max(len(issues), 1)
    aud = audit(repo, issues, labels, seed) if issues else Counter()
    tpl = template_labels(repo)
    lines = [f"### {repo}",
             f"- exactly bug {nb}, exactly enhancement {ne}, both (excluded) {len(both)}; minority share "
             f"{100 * min(nb, ne) / total:.1f}%",
             f"- type-tag titles (scorer pattern): {tagged} of {len(issues)} = {100 * tagged / total:.1f}%",
             f"- authors: {len(authors)} distinct; bot-authored {bots}; top author {top} filed {topn} "
             f"({100 * topn / total:.0f}%)",
             f"- filed since 2025-06-01: {100 * since / total:.1f}%",
             f"- label audit (seeded sample): {dict(aud)}",
             f"- issue templates that set labels today: {tpl if tpl else 'none found'}", ""]
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--out")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        print("gh is not logged in. Run: gh auth login", file=sys.stderr)
        return 1
    out: list[str] = ["# Independent verification of candidate evaluation repos", ""]
    for r in a.repos:
        print(f"checking {r} ...", file=sys.stderr)
        out += check(r, a.seed)
    text = "\n".join(out)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
