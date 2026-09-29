"""Fetches other open issues on the target repo so the advisor has something
real to compare a new issue against for duplicate detection.

Built 2026-09-29, after batch 2 of the blind test kit surfaced that the live
webhook path never populated `payload["candidates"]` at all -- only
cli.py's --candidates flag did, which is what the local blind-test harness
used, not the deployed pipeline. advisor.py's duplicate-flagging logic (see
_validate_duplicate_reference and _issue_text) was already correct and
already tested; it simply never received any candidates to compare against
in production. This is the missing half: fetch some, in the same shape
advisor.py already expects ({"number", "title", "body"} per candidate).

Deliberately narrow, same reasoning as CommentPoster in comment_poster.py:
a Protocol so the pipeline and its tests never need a real GitHub client,
and a real implementation that reuses the same TokenProvider abstraction
GitHubCommentPoster already uses, so nothing about credential handling is
duplicated or diverges between posting and fetching.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from surface_zero.comment_poster import GITHUB_API, TokenProvider

# Open AND closed issues, deliberately -- reversed 2026-09-29. Open-only was
# the original design, on the theory that a closed issue is presumably
# resolved and comparing against it is a noisier question. Reversed after a
# smoke test showed the real failure mode: a cluster's true original gets
# closed by a maintainer, its still-open satellite duplicates remain, and
# every future report in that cluster silently anchors to the stale
# satellite instead of the (more informative) closed original -- which can
# make an already-fixed bug look freshly open. Each candidate now carries
# its state and state_reason (open / closed-as-completed / closed-as-not-
# planned / closed-as-reopened) so advisor.py can decide what a match
# against a closed issue should actually communicate, rather than staying
# silent about it or treating every closed match the same way.
#
# PAGE_SIZE is the GitHub API's own per-request max, not a candidate limit --
# fetch() below pages through every issue up to CANDIDATE_HARD_CAP, it does
# not stop at one page. A single 100-item page silently dropped older issues
# (the ones most likely to be a cluster's true original) off the end once a
# repo passed 100 open issues at once; found and fixed 2026-09-29 after a
# smoke test flagged a duplicate against a non-original cluster member and
# prompted a closer look at how candidates are actually fetched.
PAGE_SIZE = 100
CANDIDATE_HARD_CAP = 1000  # sanity ceiling against a pathological repo size


class CandidateFetcher(Protocol):
    def fetch(self, *, repo: str, exclude_number: int | None) -> list[dict[str, Any]]:
        """Returns other open issues on `repo`, each as {"number", "title",
        "body"}, excluding `exclude_number` (the issue that just triggered
        this run) and excluding pull requests, which the GitHub issues API
        includes by default alongside real issues. Never raises for an
        ordinary API response; a caller that can't tolerate a fetch failure
        should wrap the call itself, same as GitHubCommentPoster's callers
        already do for posting."""
        ...


class GitHubCandidateFetcher:
    """Real implementation, real GitHub REST API. Kept narrow: read-only,
    one endpoint, no state beyond the HTTP client and the token provider."""

    def __init__(self, token: str | TokenProvider, *, client: httpx.Client | None = None):
        if not token:
            raise ValueError("GitHubCandidateFetcher requires a token")
        from surface_zero.comment_poster import StaticToken

        self._token_provider: TokenProvider = (
            token if hasattr(token, "get_token") else StaticToken(token)
        )
        self._client = client or httpx.Client(
            base_url=GITHUB_API,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )

    def fetch(self, *, repo: str, exclude_number: int | None) -> list[dict[str, Any]]:
        token = self._token_provider.get_token()
        headers = {"Authorization": f"Bearer {token}"}

        candidates: list[dict[str, Any]] = []
        page = 1
        while len(candidates) < CANDIDATE_HARD_CAP:
            response = self._client.get(
                f"/repos/{repo}/issues",
                params={
                    "state": "all",  # open and closed -- see the module docstring
                    "per_page": PAGE_SIZE,
                    "page": page,
                    "sort": "created",
                    "direction": "asc",  # oldest first -- keeps true cluster
                    # originals in the window as the repo grows, instead of
                    # letting them fall off the end of a single newest-first page
                },
                headers=headers,
            )
            response.raise_for_status()
            issues = response.json()
            if not issues:
                break

            for issue in issues:
                if "pull_request" in issue:  # the issues API includes PRs; we don't want those
                    continue
                if issue.get("number") == exclude_number:
                    continue
                candidates.append(
                    {
                        "number": issue.get("number"),
                        "title": issue.get("title", ""),
                        "body": issue.get("body") or "",
                        "state": issue.get("state", "open"),
                        "state_reason": issue.get("state_reason"),
                    }
                )

            if len(issues) < PAGE_SIZE:  # last page
                break
            page += 1

        return candidates[:CANDIDATE_HARD_CAP]

    @classmethod
    def from_env(cls) -> GitHubCandidateFetcher:
        """Same App credentials GitHubCommentPoster.from_env uses -- this
        fetcher needs only read access to Issues, which the App's existing
        Issues: Read and write install-time permission already covers, so
        no separate credential or App permission change is needed."""
        import os

        from dotenv import load_dotenv

        from surface_zero.comment_poster import GitHubAppTokenProvider

        load_dotenv()

        app_id = os.environ.get("SURFACE_ZERO_GITHUB_APP_ID")
        installation_id = os.environ.get("SURFACE_ZERO_GITHUB_INSTALLATION_ID")
        private_key = os.environ.get("SURFACE_ZERO_GITHUB_PRIVATE_KEY")
        private_key_path = os.environ.get("SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH")

        if private_key_path and not private_key:
            with open(private_key_path, encoding="utf-8") as f:
                private_key = f.read()

        missing = [
            name
            for name, value in [
                ("SURFACE_ZERO_GITHUB_APP_ID", app_id),
                ("SURFACE_ZERO_GITHUB_INSTALLATION_ID", installation_id),
                (
                    "SURFACE_ZERO_GITHUB_PRIVATE_KEY or SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH",
                    private_key,
                ),
            ]
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Missing GitHub App credentials: " + ", ".join(missing) + ". "
                "Same credentials GitHubCommentPoster.from_env needs -- see its docstring."
            )

        provider = GitHubAppTokenProvider(
            app_id=app_id,
            private_key_pem=private_key,
            installation_id=installation_id,
        )
        return cls(provider)
