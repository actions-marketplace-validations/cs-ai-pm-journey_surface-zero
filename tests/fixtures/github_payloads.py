"""Builders for realistic-enough GitHub webhook payloads, used across webhook
and pipeline tests. Trimmed to the fields webhook.py actually reads, real
GitHub payloads carry a lot more that Surface Zero has no reason to touch.
"""

from __future__ import annotations

import json
from typing import Any


def issues_opened_payload(
    *,
    title: str = "App crashes on launch",
    body: str = "Reproduces every time.",
    number: int = 42,
    repo: str = "fixture/example-repo",
    author: str = "someuser",
) -> dict[str, Any]:
    return {
        "action": "opened",
        "issue": {"number": number, "title": title, "body": body, "user": {"login": author}},
        "repository": {"full_name": repo},
    }


def issues_labeled_payload(**kw: Any) -> dict[str, Any]:
    payload = issues_opened_payload(**kw)
    payload["action"] = "labeled"
    return payload


def pull_request_opened_payload(**kw: Any) -> dict[str, Any]:
    return {
        "action": "opened",
        "pull_request": {"number": kw.get("number", 1)},
        "repository": {"full_name": kw.get("repo", "fixture/example-repo")},
    }


def as_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()
