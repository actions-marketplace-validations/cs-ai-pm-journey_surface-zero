"""Pulls MedTimer's public labels and issues (state=all, PRs excluded) via the
public GitHub REST API and saves them as JSON, plus prints a summary of what
ground truth exists. Read-only. No login needed (60 requests/hour); set
GITHUB_TOKEN in your shell to raise that, but do not paste the token anywhere.

Usage (run on your own machine, from the surface-zero folder):
    uv run python scripts/medtimer_pull.py "<output folder>"

Save the output outside the code repo (the vault), since it is another
person's issue content. The summary is what we use to write predictions.
"""

import collections
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Futsch1/medTimer"
API = "https://api.github.com"


def get(url):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            link = resp.headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
            return json.loads(resp.read()), resp.headers.get("X-RateLimit-Remaining"), next_url
    except urllib.error.HTTPError as exc:
        print(f"GitHub returned {exc.code} for {url}\n{exc.read().decode(errors='replace')[:600]}", file=sys.stderr)
        raise


def paged(path):
    """Follows the Link header's rel=next cursor instead of counting pages:
    GitHub refuses page numbers past 1,000 results on large repos."""
    out = []
    url = f"{API}{path}{'&' if '?' in path else '?'}per_page=100"
    remaining = None
    while url:
        data, remaining, url = get(url)
        out.extend(data)
        print(f"  fetched {len(out)} items so far", file=sys.stderr)
    return out, remaining


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    labels, _ = paged(f"/repos/{REPO}/labels")
    raw, remaining = paged(f"/repos/{REPO}/issues?state=all&sort=created&direction=asc")
    issues = [i for i in raw if "pull_request" not in i]

    keep = [
        {
            "number": i["number"],
            "title": i["title"],
            "body": i.get("body") or "",
            "state": i["state"],
            "state_reason": i.get("state_reason"),
            "created_at": i["created_at"],
            "closed_at": i.get("closed_at"),
            "author_association": i.get("author_association"),
            "labels": [l["name"] for l in i["labels"]],
            "comments": i["comments"],
        }
        for i in issues
    ]
    (out / "labels.json").write_text(json.dumps([{"name": l["name"], "description": l.get("description")} for l in labels], indent=2))
    (out / "issues.json").write_text(json.dumps(keep, indent=2))

    print(f"items returned by the API (issues + PRs): {len(raw)}")
    print(f"issues pulled: {len(keep)} (open {sum(i['state']=='open' for i in keep)}, closed {sum(i['state']=='closed' for i in keep)})")
    print(f"API requests remaining this hour: {remaining}")
    print("\nrepo labels:", ", ".join(l["name"] for l in labels))
    lc = collections.Counter(n for i in keep for n in i["labels"])
    print("\nlabel usage across issues:", dict(lc.most_common()))
    print("unlabeled issues:", sum(not i["labels"] for i in keep))
    print("author_association:", dict(collections.Counter(i["author_association"] for i in keep)))
    print("close state_reason values:", dict(collections.Counter(i["state_reason"] for i in keep if i["state"] == "closed")))
    print("zero-comment issues:", sum(i["comments"] == 0 for i in keep))
    dup = [i["number"] for i in keep if any("dup" in n.lower() for n in i["labels"])]
    print("issues with a duplicate-ish label:", len(dup), dup[:20])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
