"""Build a CANDIDATE LIST of repositories that fit the distribution rule in b12w2-handoff.md, section 7.

This only reads public GitHub data. It posts nothing and contacts no one. A person reviews the list
and decides who, if anyone, gets a message.

Rule (stated before looking at any result):
  public, not a fork, not archived, issues enabled, pushed within 30 days,
  30 to 1500 stars, at least 10 open issues,
  a real label set: at least 6 labels, and at least 3 of them with a description,
  a backlog with unlabelled issues: at least 30% of the most recent 30 open issues have no label,
  few maintainers: at most 5 contributors with 5 or more commits (a proxy, not a fact),
  not already contacted (see EXCLUDE), and no workflow file that looks like an existing triage bot.

Needs the GitHub CLI logged in: `gh auth status`.   Usage:
    uv run python research/scripts/find_candidates.py --out candidates.json --md candidates.md
Rate limits: search is 30 requests a minute; the script sleeps between searches.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

EXCLUDE = {  # already contacted, owned by Adam, or the evaluation repos
    "predictive-clinical-neuroscience/pcntoolkit", "yonatangross/orchestkit", "forgeas/medtimer",
    "forgeas/medtimer-replay", "forgeas/everforge", "thevpc/nuts", "cs-ai-pm-journey/surface-zero",
}
LANGUAGES = ["python", "typescript", "javascript", "go", "rust", "java"]
BOT_HINTS = ("triage", "labeler", "label-bot", "issue-labeler", "auto-label", "autolabel", "classify")


def gh(path: str, *args: str):
    """GET a GitHub API path through the logged-in gh CLI. Returns parsed JSON or None on error."""
    # -X GET is required: `gh api` switches to POST as soon as any -f field is given.
    cmd = ["gh", "api", "-X", "GET", path, *args]
    for attempt in range(3):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else None
        if "rate limit" in proc.stderr.lower() or "403" in proc.stderr:
            time.sleep(20 * (attempt + 1))
            continue
        print(f"  gh error on {path}: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return None
    print(f"  gh gave up on {path} after retries", file=sys.stderr)
    return None


def search_repos(language: str, since: str, page: int) -> list[dict]:
    q = (f"language:{language} stars:30..1500 pushed:>{since} archived:false fork:false "
         f"is:public good-first-issues:>0")
    out = gh("search/repositories", "-f", f"q={q}", "-f", "sort=updated", "-f", "per_page=30", "-f", f"page={page}")
    return (out or {}).get("items", [])


def check(repo: dict) -> dict | None:
    """Return a candidate record, or None with the reason stored under 'why_not' via the caller."""
    name = repo["full_name"]
    if name.lower() in EXCLUDE:
        return {"repo": name, "why_not": "already contacted or owned"}
    if not repo.get("has_issues") or repo.get("open_issues_count", 0) < 10:
        return {"repo": name, "why_not": "fewer than 10 open issues or issues disabled"}

    labels = gh(f"repos/{name}/labels", "-f", "per_page=100") or []
    described = sum(1 for l in labels if (l.get("description") or "").strip())
    if len(labels) < 6 or described < 3:
        return {"repo": name, "why_not": f"label set too thin ({len(labels)} labels, {described} described)"}

    issues = gh(f"repos/{name}/issues", "-f", "state=open", "-f", "per_page=30", "-f", "sort=created") or []
    issues = [i for i in issues if "pull_request" not in i]
    if len(issues) < 10:
        return {"repo": name, "why_not": "fewer than 10 recent open issues"}
    unlabeled = sum(1 for i in issues if not i.get("labels"))
    share = unlabeled / len(issues)
    if share < 0.30:
        return {"repo": name, "why_not": f"only {share:.0%} of recent open issues are unlabelled"}

    contribs = gh(f"repos/{name}/contributors", "-f", "per_page=100") or []
    active = sum(1 for c in contribs if c.get("contributions", 0) >= 5)
    if active > 5:
        return {"repo": name, "why_not": f"{active} contributors with 5+ commits"}

    wf = gh(f"repos/{name}/contents/.github/workflows") or []
    hints = [w["name"] for w in wf if isinstance(w, dict) and any(h in w["name"].lower() for h in BOT_HINTS)]
    if hints:
        return {"repo": name, "why_not": f"looks like it already has a triage workflow: {hints}"}

    return {
        "repo": name, "url": repo["html_url"], "stars": repo["stargazers_count"],
        "owner_type": repo["owner"]["type"], "open_issues": repo["open_issues_count"],
        "recent_unlabelled_share": round(share, 2), "labels": len(labels), "labels_described": described,
        "active_contributors_proxy": active, "pushed_at": repo["pushed_at"],
        "has_discussions": repo.get("has_discussions", False), "description": repo.get("description"),
        "language": repo.get("language"), "ok": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="candidates.json")
    ap.add_argument("--md", default="candidates.md")
    ap.add_argument("--pages", type=int, default=2, help="search pages per language (30 repos each)")
    ap.add_argument("--max-checks", type=int, default=150, help="stop after checking this many repos")
    a = ap.parse_args()
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        print("gh is not logged in. Run: gh auth login", file=sys.stderr)
        return 1

    since = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
    seen: set[str] = set()
    results: list[dict] = []
    checked = 0
    for lang in LANGUAGES:
        for page in range(1, a.pages + 1):
            found = search_repos(lang, since, page)
            print(f"search {lang} page {page}: {len(found)} repos")
            for repo in found:
                if repo["full_name"] in seen or checked >= a.max_checks:
                    continue
                seen.add(repo["full_name"])
                checked += 1
                rec = check(repo)
                if rec:
                    results.append(rec)
                    print(("OK   " if rec.get("ok") else "skip ") + rec["repo"] + ("" if rec.get("ok") else f"  ({rec['why_not']})"))
            time.sleep(3)
    ok = sorted((r for r in results if r.get("ok")), key=lambda r: (-r["recent_unlabelled_share"], r["stars"]))
    with open(a.out, "w") as f:
        json.dump({"rule_date": datetime.now(UTC).isoformat(), "checked": checked, "passed": ok,
                   "rejected": [r for r in results if not r.get("ok")]}, f, indent=2)
    with open(a.md, "w") as f:
        f.write(f"# Candidate repositories ({len(ok)} passed of {checked} checked)\n\n"
                "Screened by script only. A person must still read each repo and decide. Checks the script cannot make: "
                "tone of the maintainers, whether they welcome outside tools, whether the labels fit the issues.\n\n"
                "| repo | stars | owner | open issues | recent unlabelled | labels (described) | active contributors (proxy) | discussions |\n"
                "|---|---|---|---|---|---|---|---|\n")
        for r in ok:
            f.write(f"| [{r['repo']}]({r['url']}) | {r['stars']} | {r['owner_type']} | {r['open_issues']} | "
                    f"{r['recent_unlabelled_share']:.0%} | {r['labels']} ({r['labels_described']}) | "
                    f"{r['active_contributors_proxy']} | {'yes' if r['has_discussions'] else 'no'} |\n")
    print(f"\nchecked {checked}, passed {len(ok)}. Wrote {a.out} and {a.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
