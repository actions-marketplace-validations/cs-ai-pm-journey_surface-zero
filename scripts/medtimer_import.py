"""Plans and performs the MedTimer fork replay. Writes ONLY to a fork you name.

  # 1. Plan (no network, safe): shows counts and 3 neutralized samples
  uv run python scripts/medtimer_import.py plan <data folder>

  # 2. Seed (no bot yet): creates the older context issues on the fork
  GITHUB_TOKEN=... uv run python scripts/medtimer_import.py seed <data folder> --repo ForgeAS/<fork>

  # 3. Install the app on the fork, then replay (the bot answers each one)
  GITHUB_TOKEN=... uv run python scripts/medtimer_import.py replay <data folder> --repo ForgeAS/<fork>

Copies are opened from YOUR account (the token's owner). That matters: the
webhook skips Dependabot/Renovate authors, and the bot's own identity is not
used to open issues. The token should be a fine-grained PAT limited to the
fork, Issues: read and write only. mapping.json records original -> fork
issue numbers so reruns never duplicate and results can be scored.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from surface_zero.issue_import import assert_safe_target, build_copy, original_title, select_sets

DUP_PAIRS = {853: 298, 994: 931, 924: 912, 681: 413}  # child -> original (from pull 2)
API = "https://api.github.com"


def load(data):
    d = Path(data)
    return json.loads((d / "issues.json").read_text()), json.loads((d / "label_events.json").read_text())


def call(method, path, payload=None):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("set GITHUB_TOKEN first (read -rs GITHUB_TOKEN && export GITHUB_TOKEN)")
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        print(f"GitHub {exc.code}: {exc.read().decode(errors='replace')[:300]}", file=sys.stderr)
        raise


def titles_for(issue, renames):
    return original_title(issue, renames.get(str(issue["number"]), []))


def run(args):
    issues, events = load(args.data)
    rpath = Path(args.data) / "rename_events.json"
    if not rpath.exists() and args.cmd != "plan":
        sys.exit("rename_events.json missing: run scripts/medtimer_titles_pull.py first (title leak guard)")
    renames = json.loads(rpath.read_text()) if rpath.exists() else {}
    byn = {i["number"]: i for i in issues}
    sets = select_sets(issues, events, recent=60, duplicate_pairs=DUP_PAIRS)
    which = {"seed": sets["seed"], "replay": sets["test"]}.get(args.cmd)

    if args.cmd == "plan":
        print(f"replay (bot answers): {len(sets['test'])}   seed (no bot): {len(sets['seed'])}")
        print("corrections:", sets["corrections"], " duplicate children:", sets["duplicate_children"])
        for n in sets["test"][:3]:
            t, b = build_copy(byn[n], title=titles_for(byn[n], renames))
            print(f"\n--- original #{n} -> title: {t}\n{b[:500]}")
        return

    assert_safe_target(args.repo)
    mapping_path = Path(args.data) / f"mapping-{args.repo.replace('/', '__')}.json"
    mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
    delay = 3 if args.cmd == "seed" else args.delay
    for n in which:
        if str(n) in mapping:
            continue
        title, body = build_copy(byn[n], title=titles_for(byn[n], renames))
        created = call("POST", f"/repos/{args.repo}/issues", {"title": title, "body": body})
        mapping[str(n)] = created["number"]
        mapping_path.write_text(json.dumps(mapping, indent=2))
        orig = byn[n]
        if args.cmd == "seed" and orig["state"] == "closed":
            call("PATCH", f"/repos/{args.repo}/issues/{created['number']}",
                 {"state": "closed", "state_reason": orig["state_reason"] or "completed"})
        print(f"original #{n} -> {args.repo}#{created['number']}")
        time.sleep(delay)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "seed", "replay"])
    ap.add_argument("data")
    ap.add_argument("--repo")
    ap.add_argument("--delay", type=int, default=25, help="seconds between replayed issues so each answer finishes first")
    a = ap.parse_args()
    if a.cmd != "plan" and not a.repo:
        sys.exit("--repo owner/fork is required")
    run(a)
