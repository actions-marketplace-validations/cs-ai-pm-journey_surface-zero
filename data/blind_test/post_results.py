"""Step 2: post each already-scored blind-test result as a real comment on
its matching GitHub issue, using the exact same outbound handler (kill
switch + render_comment + GitHubCommentPoster) the live webhook path uses.
Abstained results are skipped -- runner.run_request never calls outbound for
an abstention, so this mirrors that rather than special-casing it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from surface_zero.comment_poster import GitHubAppTokenProvider, GitHubCommentPoster
from surface_zero.killswitch import KillSwitch
from surface_zero.pipeline import build_outbound_handler
from surface_zero.runner import AgentResult, RunRequest

REPO = "ForgeAS/everForge"
PRIVATE_KEY_PATH = "surface-zero-advisor.2026-09-24.private-key.pem"

provider = GitHubAppTokenProvider(
    app_id="5060119",
    private_key_pem=Path(PRIVATE_KEY_PATH).read_text(),
    installation_id="164415198",
)
poster = GitHubCommentPoster(provider)
kill_switch = KillSwitch()  # data/kill_switch.json, relative to cwd -- confirmed absent/inactive
outbound = build_outbound_handler(poster, kill_switch)

mapping = json.loads(Path("data/blind_test/issue_number_map.json").read_text())
results_dir = Path("data/blind_test/results")

posted, skipped, failed = [], [], []
for i in range(1, 17):
    local = json.loads((results_dir / f"issue_{i:02d}.json").read_text())
    run = local["run"]
    status = run["status"]
    github_number = mapping[str(i)]

    if status == "abstained":
        skipped.append((i, github_number, "advisor abstained, no comment in the real pipeline either"))
        continue

    result = AgentResult(response=run["response"], abstained=False)
    request = RunRequest(
        idempotency_key=f"blind-test-backfill-{i}",
        event_type="issue.opened",
        target=REPO,
        payload={"number": github_number},
    )
    try:
        outcome = outbound(request, result)
        posted.append((i, github_number, outcome.response_patch.get("comment_url")))
        print(f"local #{i:2d} -> github #{github_number}: posted -> {outcome.response_patch.get('comment_url')}")
    except Exception as exc:  # noqa: BLE001
        failed.append((i, github_number, str(exc)))
        print(f"local #{i:2d} -> github #{github_number}: FAILED -> {exc}")

print()
print(f"posted: {len(posted)}, skipped (abstained): {len(skipped)}, failed: {len(failed)}")
for i, n, reason in skipped:
    print(f"  skipped local #{i} / github #{n}: {reason}")
