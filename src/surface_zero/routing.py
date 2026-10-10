"""Per-repo routing for the two components that talk to GitHub with an
installation token: the comment poster and the candidate fetcher.

Why a routing layer instead of changing those classes: each GitHub App
installation has its own access token, and a token for one installation can
only see that installation's repos. Rather than teach GitHubCommentPoster and
GitHubCandidateFetcher about multiple installations, we keep each exactly as
tested (one token provider, one installation) and build one pair per
installation. These wrappers pick the right pair from the repo name.

The repo -> installation mapping comes from configuration, never from the
webhook payload. A payload is attacker-influenced input; the operator's own
config is not. A repo with no configured installation fails loudly
(UnroutedRepoError) rather than falling back to some other installation's
token, which would either 404 or, worse, post through the wrong identity.

Config format (env SURFACE_ZERO_REPO_INSTALLATIONS):
    "ForgeAS/everForge=12345678,thevpc/nuts=87654321"
Repo names are case-insensitive, installation ids must be numeric.
"""

from __future__ import annotations

from typing import Any

from surface_zero.candidate_fetcher import CandidateFetcher, GitHubCandidateFetcher
from surface_zero.comment_poster import (
    CommentPoster,
    GitHubAppTokenProvider,
    GitHubCommentPoster,
)


class UnroutedRepoError(Exception):
    """No installation is configured for this repo."""


class InstallationConfigError(ValueError):
    """SURFACE_ZERO_REPO_INSTALLATIONS is malformed."""


def parse_repo_installations(raw: str | None) -> dict[str, str] | None:
    """None when unset or blank (caller keeps the single-installation path).
    Raises on any malformed entry rather than skipping it: a typo in a
    security-relevant mapping should stop startup, not silently drop a repo.
    """
    if raw is None or not raw.strip():
        return None
    mapping: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        repo, sep, installation_id = part.partition("=")
        repo = repo.strip()
        installation_id = installation_id.strip()
        if not sep or "/" not in repo or not installation_id.isdigit():
            raise InstallationConfigError(
                f"bad entry {part!r}: expected 'owner/name=<numeric installation id>'"
            )
        key = repo.lower()
        if key in mapping:
            raise InstallationConfigError(f"repo {repo!r} appears more than once")
        mapping[key] = installation_id
    if not mapping:
        return None
    return mapping


class RoutingCommentPoster:
    def __init__(self, posters: dict[str, CommentPoster]):
        self._posters = {repo.lower(): p for repo, p in posters.items()}

    def post_comment(self, *, repo: str, issue_number: int, body: str) -> str:
        poster = self._posters.get(repo.lower())
        if poster is None:
            raise UnroutedRepoError(f"no installation configured for {repo!r}")
        return poster.post_comment(repo=repo, issue_number=issue_number, body=body)


class RoutingCandidateFetcher:
    def __init__(self, fetchers: dict[str, CandidateFetcher]):
        self._fetchers = {repo.lower(): f for repo, f in fetchers.items()}

    def fetch(self, *, repo: str, exclude_number: int | None) -> list[dict[str, Any]]:
        fetcher = self._fetchers.get(repo.lower())
        if fetcher is None:
            raise UnroutedRepoError(f"no installation configured for {repo!r}")
        return fetcher.fetch(repo=repo, exclude_number=exclude_number)


def build_routed_clients(
    *,
    app_id: str,
    private_key_pem: str,
    repo_installations: dict[str, str],
) -> tuple[RoutingCommentPoster, RoutingCandidateFetcher]:
    """One token provider per distinct installation, shared by that
    installation's poster and fetcher so a token is minted once per hour per
    installation, not once per component."""
    providers: dict[str, GitHubAppTokenProvider] = {}
    posters: dict[str, CommentPoster] = {}
    fetchers: dict[str, CandidateFetcher] = {}
    for repo, installation_id in repo_installations.items():
        provider = providers.get(installation_id)
        if provider is None:
            provider = GitHubAppTokenProvider(
                app_id=app_id,
                private_key_pem=private_key_pem,
                installation_id=installation_id,
            )
            providers[installation_id] = provider
        posters[repo] = GitHubCommentPoster(provider)
        fetchers[repo] = GitHubCandidateFetcher(provider)
    return RoutingCommentPoster(posters), RoutingCandidateFetcher(fetchers)


def load_app_credentials_from_env() -> tuple[str, str]:
    import os

    from dotenv import load_dotenv

    load_dotenv()
    app_id = os.environ.get("SURFACE_ZERO_GITHUB_APP_ID")
    private_key = os.environ.get("SURFACE_ZERO_GITHUB_PRIVATE_KEY")
    private_key_path = os.environ.get("SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH")
    if private_key_path and not private_key:
        with open(private_key_path, encoding="utf-8") as f:
            private_key = f.read()
    missing = [
        name
        for name, value in [
            ("SURFACE_ZERO_GITHUB_APP_ID", app_id),
            ("SURFACE_ZERO_GITHUB_PRIVATE_KEY or SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH", private_key),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError("Missing GitHub App credentials: " + ", ".join(missing))
    return app_id, private_key
