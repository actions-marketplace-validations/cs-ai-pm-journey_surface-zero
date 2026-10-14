"""Step 1: list real issues on ForgeAS/everForge via the App, match each to
our local blind-test result by exact title, print the mapping for a sanity
check before anything gets posted."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from surface_zero.comment_poster import GitHubAppTokenProvider, GitHubCommentPoster

PRIVATE_KEY_PATH = "surface-zero-advisor.2026-09-24.private-key.pem"
REPO = "ForgeAS/everForge"

provider = GitHubAppTokenProvider(
    app_id="5060119",
    private_key_pem=Path(PRIVATE_KEY_PATH).read_text(),
    installation_id="164415198",
)
poster = GitHubCommentPoster(provider)

# Reuse the poster's own httpx client + token so this hits the same
# authenticated API surface as the real bot.
resp = poster._client.get(
    f"/repos/{REPO}/issues",
    params={"state": "all", "per_page": 100},
    headers=poster._auth_headers(),
)
resp.raise_for_status()
issues = resp.json()

results_dir = Path("research/blind_test/results")
mapping = {}
unmatched_local = []
for i in range(1, 17):
    local = json.loads((results_dir / f"issue_{i:02d}.json").read_text())
    payload_title = None
    # title isn't stored in the response, pull it back from run_blind_test.sh's
    # own titles list, duplicated here just for matching purposes.
    mapping[i] = local

TITLES = {
    1: 'app crashes when parsing yt video link',
    2: '"Extract Workflows" button not responding',
    3: 'Saved Reddit automation tidbits from yesterday completely vanished / library reset?',
    4: 'Is saved library items disappearing a known issue?',
    5: 'Feature request: Export parsed ideas and workflows to CSV / Excel',
    6: 'Dark mode please!!',
    7: 'Auto notifications when new market openings are extracted / email alerts?',
    8: "Bug: App won't automatically execute the extracted AI workflows in my Zapier account",
    9: 'Getting Started guide step 3 references missing "API Keys" tab in Settings',
    10: 'How do I filter saved ideas by source (Reddit vs YouTube)?',
    11: 'Reddit thread comment depth parsing not working as documented?',
    12: 'QoL: Remember last selected library sorting option across sessions',
    13: 'Blank screen crash when sharing long YouTube playlist link',
    14: 'Reddit parsing taking 2-3 minutes per post now (used to take 5 seconds)',
    15: 'Need to transfer EverForge account ownership to new team lead / billing invoice request',
    16: 'UI glitch: "Market Openings" header overlaps badge count on mobile',
}

by_title = {issue["title"]: issue["number"] for issue in issues}

matches = {}
for i, title in TITLES.items():
    real_number = by_title.get(title)
    matches[i] = real_number
    status = "OK" if real_number is not None else "!! NOT FOUND"
    print(f"local #{i:2d} -> github #{real_number}  [{status}]  {title[:70]}")

Path("research/blind_test/issue_number_map.json").write_text(json.dumps(matches, indent=2))
missing = [i for i, n in matches.items() if n is None]
print()
print(f"matched {16 - len(missing)}/16" + (f", MISSING: {missing}" if missing else ", all matched"))
