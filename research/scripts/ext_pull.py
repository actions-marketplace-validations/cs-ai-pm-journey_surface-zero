"""Pull the issues for the external replication (prereg-external-replication.md). Read-only.

Usage:  uv run python research/scripts/ext_pull.py OUT_DIR
Writes  OUT_DIR/<owner>__<repo>/issues.json  (every exactly-one-label issue, with a `stranger` flag)
        OUT_DIR/manifest.json and OUT_DIR/pull-summary.md  (the counts the pre-registration says are
        recorded before any model call).
Maintainer set M of a repo = the owner's login plus any author who filed 20% or more of that repo's
exactly-one-label issues. stranger = author not in M and not a bot. Only stranger issues are run later.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_eval_repo import is_bot, paged  # noqa: E402

REPOS = {
    "usetrmnl/terminus": "primary",
    "ChiLiubio/microeco": "primary",
    "Vexu/arocc": "primary",
    "DevEmperor/DictateKeyboard": "secondary",
    "VoidXH/Cavern": "secondary",
    "lyskouski/app-finance": "secondary",
    "ivmartel/dwv": "secondary",
    "tormozit/EDT.Comfort": "secondary",
}
MAINTAINER_SHARE = 0.20


def pull(repo: str) -> dict:
    by_label: dict[str, dict[int, dict]] = {}
    for lab in ("bug", "enhancement"):
        rows = paged(f"repos/{repo}/issues", f"labels={lab}", "state=all")
        by_label[lab] = {i["number"]: i for i in rows if "pull_request" not in i}
    both = set(by_label["bug"]) & set(by_label["enhancement"])
    issues = []
    for lab, rows in by_label.items():
        for n, i in rows.items():
            if n in both:
                continue
            issues.append({"number": n, "title": i["title"], "body": i.get("body") or "", "label": lab,
                           "author": i["user"]["login"], "author_type": i["user"].get("type"),
                           "created_at": i["created_at"], "state": i["state"]})
    issues.sort(key=lambda x: x["number"])
    share = Counter(i["author"].lower() for i in issues)
    owner = repo.split("/")[0].lower()
    maint = {owner} | {a for a, c in share.items() if c / max(len(issues), 1) >= MAINTAINER_SHARE}
    for i in issues:
        i["stranger"] = i["author"].lower() not in maint and not is_bot(i["author"], i["author_type"])
    return {"issues": issues, "both": len(both), "maintainers": sorted(maint)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    a = ap.parse_args()
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        print("gh is not logged in. Run: gh auth login", file=sys.stderr)
        return 1
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"pulled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "repos": {}}
    lines = ["# External replication: pull summary (recorded before any model call)", "",
             f"Pulled at {manifest['pulled_at']}.", "",
             "| repo | group | exactly-one issues | both (excluded) | maintainers M | stranger-filed | stranger bug | stranger enh | stranger since 2025-06-01 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for repo, group in REPOS.items():
        print(f"pulling {repo} ...", file=sys.stderr)
        p = pull(repo)
        d = out / repo.replace("/", "__")
        d.mkdir(exist_ok=True)
        (d / "issues.json").write_text(json.dumps(p["issues"], indent=1))
        s = [i for i in p["issues"] if i["stranger"]]
        sb = sum(1 for i in s if i["label"] == "bug")
        recent = sum(1 for i in s if i["created_at"] >= "2025-06-01")
        manifest["repos"][repo] = {"group": group, "total": len(p["issues"]), "both": p["both"],
                                   "maintainers": p["maintainers"], "stranger": len(s),
                                   "stranger_bug": sb, "stranger_enh": len(s) - sb}
        lines.append(f"| {repo} | {group} | {len(p['issues'])} | {p['both']} | {', '.join(p['maintainers'])} | "
                     f"{len(s)} | {sb} | {len(s) - sb} | {recent} |")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (out / "pull-summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
