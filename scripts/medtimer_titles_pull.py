"""Third MedTimer pull: the title each replay/seed issue was FILED with.

The current title may have been rewritten by the maintainer after he reclassified
the issue, which would leak the answer. GitHub keeps each retitle as a `renamed`
event; we save them. Read-only, public API. Needs a login (about 126 requests):

    read -rs GITHUB_TOKEN && export GITHUB_TOKEN     # then paste the token ONCE at the blank prompt
    uv run python scripts/medtimer_titles_pull.py "<medtimer-fork folder>"
    unset GITHUB_TOKEN
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

from surface_zero.issue_import import select_sets

REPO = "Futsch1/medTimer"
DUP_PAIRS = {853: 298, 994: 931, 924: 912, 681: 413}


def get(url):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main(folder):
    d = Path(folder)
    issues = json.loads((d / "issues.json").read_text())
    events = json.loads((d / "label_events.json").read_text())
    sets = select_sets(issues, events, recent=60, duplicate_pairs=DUP_PAIRS)
    out = {}
    for n in sets["test"] + sets["seed"]:
        evs = get(f"https://api.github.com/repos/{REPO}/issues/{n}/events?per_page=100")
        out[str(n)] = [
            {"event": "renamed", "from": e["rename"]["from"], "to": e["rename"]["to"], "at": e["created_at"]}
            for e in evs if e["event"] == "renamed"
        ]
    (d / "rename_events.json").write_text(json.dumps(out, indent=2))
    renamed = {k: v for k, v in out.items() if v}
    print(f"issues checked: {len(out)}; retitled at least once: {len(renamed)}")
    print("retitled corrections:", [n for n in sets["corrections"] if str(n) in renamed])
    print("retitled duplicate children:", [n for n in sets["duplicate_children"] if str(n) in renamed])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
