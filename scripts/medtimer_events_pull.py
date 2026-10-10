"""Second MedTimer pull: who applied each label, and what each
closed-as-duplicate issue was a duplicate of. Read-only, public API.

Needs a GitHub login for rate limits (about 60 requests; the anonymous cap is 60/hour
and a first pull already used some). With the gh CLI signed in:
    GITHUB_TOKEN=$(gh auth token) uv run python scripts/medtimer_events_pull.py "<output folder>"
Never paste the token anywhere. Output folder = the same vault folder as the first pull.
"""

import collections
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Futsch1/medTimer"
OWNER = "Futsch1"
API = "https://api.github.com"


def get(url):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            next_url = None
            for part in resp.headers.get("Link", "").split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
            return json.loads(resp.read()), resp.headers.get("X-RateLimit-Remaining"), next_url
    except urllib.error.HTTPError as exc:
        print(f"GitHub returned {exc.code} for {url}\n{exc.read().decode(errors='replace')[:400]}", file=sys.stderr)
        raise


def paged(url):
    out = []
    while url:
        data, remaining, url = get(url)
        out.extend(data)
        print(f"  {len(out)} items, {remaining} requests left", file=sys.stderr)
    return out


def main(out_dir):
    out = Path(out_dir)
    issues = {i["number"]: i for i in json.loads((out / "issues.json").read_text())}

    raw = paged(f"{API}/repos/{REPO}/issues/events?per_page=100")
    events = []
    for e in raw:
        issue = e.get("issue") or {}
        n = issue.get("number")
        if n not in issues or "pull_request" in issue:
            continue
        if e["event"] in ("labeled", "unlabeled", "closed", "reopened"):
            events.append({
                "issue": n,
                "event": e["event"],
                "actor": (e.get("actor") or {}).get("login"),
                "label": (e.get("label") or {}).get("name"),
                "at": e["created_at"],
            })
    (out / "label_events.json").write_text(json.dumps(events, indent=2))

    dup_numbers = [n for n, i in issues.items() if i["state_reason"] == "duplicate"]
    dup = {}
    for n in dup_numbers:
        comments = paged(f"{API}/repos/{REPO}/issues/{n}/comments?per_page=100")
        dup[n] = [{"user": c["user"]["login"], "body": c["body"][:400], "at": c["created_at"]} for c in comments]
    (out / "duplicate_comments.json").write_text(json.dumps(dup, indent=2))

    label_events = [e for e in events if e["event"] in ("labeled", "unlabeled")]
    print(f"\nlabel events on issues: {len(label_events)}")
    print("label events by actor:", dict(collections.Counter(e["actor"] for e in label_events).most_common(8)))
    by_issue = collections.defaultdict(list)
    for e in label_events:
        by_issue[e["issue"]].append(e)
    touched = {
        n for n, evs in by_issue.items()
        if any(e["actor"] == OWNER for e in evs) and issues[n]["author_association"] != "OWNER"
    }
    print(f"issues by outside authors where {OWNER} applied or removed a label: {len(touched)}")
    changed = {
        n for n in touched
        if any(e["event"] == "unlabeled" and e["actor"] == OWNER for e in by_issue[n])
    }
    print(f"...of which {OWNER} REMOVED a label (a correction): {len(changed)} {sorted(changed)[:15]}")
    print("duplicate-closed issues with comments fetched:", len(dup), sorted(dup))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
