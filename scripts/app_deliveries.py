"""Shows how the surface-zero-advisor GitHub App is configured, straight from
GitHub, and lists its recent webhook deliveries if the App has a webhook.
Uses the App's own credentials (.env or the .pem in this folder); prints no
secrets. Read-only.

    uv run python scripts/app_deliveries.py
"""

import glob
import json
import os
import time
import urllib.error
import urllib.request

import jwt
from dotenv import load_dotenv

load_dotenv()
app_id = os.environ["SURFACE_ZERO_GITHUB_APP_ID"]
key = os.environ.get("SURFACE_ZERO_GITHUB_PRIVATE_KEY")
if not key:
    path = os.environ.get("SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH") or ""
    if not path or not os.path.exists(path):
        found = sorted(glob.glob("*.private-key.pem"))
        if not found:
            raise SystemExit("no private key: set SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH or keep the .pem here")
        path = found[0]
    with open(path, encoding="utf-8") as f:
        key = f.read()

now = int(time.time())
token = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, key, algorithm="RS256")


def get(path):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:200]


code, app = get("/app")
print(f"/app -> {code}")
if code == 200:
    print("  slug:", app["slug"], "| owner:", app["owner"]["login"], "| public:", app.get("public"))
    print("  subscribed events:", app["events"])
    print("  permissions:", app["permissions"])

code, cfg = get("/app/hook/config")
print(f"\n/app/hook/config -> {code}")
if code == 200:
    print("  App-level webhook url:", cfg.get("url") or "(none set)", "| content_type:", cfg.get("content_type"))
else:
    print("  ", cfg)

code, installs = get("/app/installations")
print(f"\n/app/installations -> {code}")
if code == 200:
    for i in installs:
        print(f"  installation {i['id']} on {i['account']['login']}: repository_selection={i['repository_selection']}, "
              f"events={i['events']}, suspended={i.get('suspended_at')}")

code, rows = get("/app/hook/deliveries?per_page=30")
print(f"\n/app/hook/deliveries -> {code}")
if code == 200:
    print(f"{'delivered_at (UTC)':<22} {'event':<14} {'action':<10} {'http':<5} {'status':<10} repo_id        installation")
    for r in rows:
        print(f"{r['delivered_at']:<22} {r['event']:<14} {str(r['action']):<10} {r['status_code']:<5} "
              f"{r['status']:<10} {str(r['repository_id']):<14} {r['installation_id']}")
else:
    print("  ", rows)
