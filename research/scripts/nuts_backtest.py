"""Backtest 1 for thevpc/nuts: runs the advisor on 14 closed issues' titles
against the maintainer's real labels. Titles only (no bodies). Uses a throwaway
ledger so nothing touches data/ledger.db. Run: uv run python research/scripts/nuts_backtest.py
Predictions were written first: see the vault's nuts-backtest-1-predictions.md.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = [
    (320, "remove users from repository and centralize security", {"api-feature"}),
    (319, "Add support for named credentials (wallet)", {"api-feature"}),
    (312, "issue when rendering tree format", {"bug"}),
    (311, "Check (or add support) for installing jmeld", {"app"}),
    (310, "Check (or add support) for installing spring-boot-cli", {"app"}),
    (309, "Check (or add support) for installing JDiskReport", {"app"}),
    (303, "Check (or add support) for installing derby", {"app"}),
    (302, "Check (or add support) for installing jetty", {"app"}),
    (301, "Check (or add support) for installing tomcat", {"app"}),
    (277, "Make Memory Workspace work properly", {"architecture", "bug"}),
    (168, "support for maven settings.xml", {"enhancement"}),
    (167, "Add support to Chronometer/Task Monitoring/ Progress Monitoring", {"api-feature"}),
    (116, "display in log an info command 'appId'", {"enhancement"}),
    (115, "JDK 9+ Module Path support", {"enhancement"}),
]

db = str(Path(tempfile.mkdtemp()) / "backtest.db")
hits = 0
hits_non_app = 0
rows = []
for number, title, truth in CASES:
    proc = subprocess.run(
        ["uv", "run", "surface-zero", "advise", "--db", db,
         "--labels", "data/label_sets/nuts.json", "--title", title,
         "--key", f"nuts-bt1-{number}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"#{number} FAILED: {proc.stderr.strip()[-300:]}", file=sys.stderr)
        rows.append((number, "ERROR", truth, False))
        continue
    run = json.loads(proc.stdout)["run"]
    resp = run.get("response") or {}
    got = resp.get("suggested_label") or f"({run['status']})"
    ok = got in truth
    hits += ok
    if "app" not in truth:
        hits_non_app += ok
    rows.append((number, got, truth, ok))

for number, got, truth, ok in rows:
    print(f"#{number:<4} got={got:<14} truth={'/'.join(sorted(truth)):<22} {'OK' if ok else 'MISS'}")
print(f"\nTotal: {hits}/{len(CASES)}   Excluding the six 'app' rows: {hits_non_app}/{len(CASES) - 6}")
