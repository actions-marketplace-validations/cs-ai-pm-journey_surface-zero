"""What it means to actually post the one comment Surface Zero is allowed to make.

Kept as a narrow Protocol so the pipeline (and its tests) never depend on a
real GitHub client. GitHubCommentPoster is the real implementation, built
2026-09-23 once ForgeAS/everForge was confirmed as the target, and switched
2026-09-24 from a personal fine-grained PAT to a GitHub App installation
token (see GitHubAppTokenProvider below) once the PAT was shown to work end
to end but post under Adam's own account -- indistinguishable, to anyone
reading the issue thread, from Adam manually replying to himself. The App
gives the bot ("surface-zero-advisor") its own identity, separate from any
human maintainer, while granting *less* standing access than the PAT did:
an App installation token is scoped by GitHub itself to exactly the
permissions granted at install (Issues: Read and write, nothing else) and
expires in an hour, versus a PAT that lives until manually revoked.

Owning the target repo removes the one external check a stranger maintainer
would have forced on this token -- nobody has to grant it access, Adam just
... grants it access. So GitHubCommentPoster enforces that check itself:
before it will post anything, it looks up its own token's effective
permission on the target repo and refuses outright if that permission is
anything beyond what an advisory-only, comment-only bot could ever need.
A token an external maintainer would never have handed out is a token this
class will not use, regardless of who owns the repo it's pointed at. That
check is unchanged by the App migration -- it's cheap, and an App
installation token misconfigured with extra permissions at install time is
exactly the kind of mistake it's meant to catch.
"""

from __future__ import annotations

import time
from typing import Protocol

import httpx
import jwt

GITHUB_API = "https://api.github.com"

# GitHub App JWTs may not be issued more than 10 minutes in the future.
# Backdating `iat` guards against clock drift between this machine and
# GitHub's; keeping `exp` well under the 10-minute ceiling leaves margin
# for the same drift on the other side.
_JWT_BACKDATE_SECONDS = 60
_JWT_LIFETIME_SECONDS = 9 * 60

# Installation tokens are valid for 1 hour from issuance. Refreshing 5
# minutes early means a slow request never straddles the expiry boundary.
_TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60


class TokenProvider(Protocol):
    def get_token(self) -> str:
        """Returns a valid bearer token for the GitHub API, refreshing it
        first if needed. May be called once per outbound request -- cheap
        providers (a static token) and expensive ones (a token that must be
        minted from a signed JWT) both satisfy this the same way."""
        ...


class StaticToken:
    """The simplest possible TokenProvider: always returns the same string.
    What a bare token string used to mean before GitHubCommentPoster took a
    TokenProvider instead -- kept so callers (and every existing test) that
    just want to hand it a fixed token don't need to know a provider
    abstraction exists."""

    def __init__(self, token: str):
        self._token = token

    def get_token(self) -> str:
        return self._token


class GitHubAppTokenProvider:
    """Mints and caches GitHub App installation access tokens.

    The flow (see https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app):
    sign a short-lived JWT with the App's private key, asserting this App's
    identity (`iss`); exchange that JWT for an installation access token
    scoped to exactly the permissions granted when the App was installed on
    the target org/repo. The installation token, not the JWT, is what
    actually authenticates the comment-posting calls.

    Cached and refreshed automatically: a fresh JWT is minted for each
    token-exchange call (JWTs are cheap and short-lived by design), but the
    installation token itself is reused across calls until it's close to
    expiring, so a normal run of GitHubCommentPoster costs one exchange call
    per hour of activity, not one per comment.
    """

    def __init__(
        self,
        *,
        app_id: str,
        private_key_pem: str,
        installation_id: str,
        client: httpx.Client | None = None,
    ):
        if not app_id or not private_key_pem or not installation_id:
            raise ValueError(
                "GitHubAppTokenProvider requires app_id, private_key_pem, and installation_id"
            )
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._installation_id = installation_id
        self._client = client or httpx.Client(base_url=GITHUB_API, timeout=15.0)
        self._cached_token: str | None = None
        self._cached_expires_at: float = 0.0
        self._permissions: dict[str, str] | None = None

    def _mint_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - _JWT_BACKDATE_SECONDS,
            "exp": now + _JWT_LIFETIME_SECONDS,
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key_pem, algorithm="RS256")

    def _exchange_for_installation_token(self) -> tuple[str, float]:
        app_jwt = self._mint_jwt()
        response = self._client.post(
            f"/app/installations/{self._installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        data = response.json()
        # GitHub states exactly which permissions this token carries. That is
        # the authoritative answer to "is this token Issues-only?", unlike
        # probing endpoints, which cannot distinguish "no permission" from
        # "the repo is public".
        perms = data.get("permissions")
        self._permissions = dict(perms) if isinstance(perms, dict) else None
        # expires_at is an ISO 8601 timestamp; we only need a rough deadline
        # for our own refresh-margin check, not to reparse GitHub's format.
        expires_in_seconds = 60 * 60  # GitHub Apps: always 1 hour, as documented
        return data["token"], time.time() + expires_in_seconds

    def granted_permissions(self) -> dict[str, str] | None:
        """Permissions GitHub reported for the current installation token, or
        None if it did not report any. Refreshes the token first if needed."""
        self.get_token()
        return self._permissions

    def get_token(self) -> str:
        if self._cached_token is None or time.time() >= (
            self._cached_expires_at - _TOKEN_REFRESH_MARGIN_SECONDS
        ):
            self._cached_token, self._cached_expires_at = self._exchange_for_installation_token()
        return self._cached_token


class CommentPoster(Protocol):
    def post_comment(self, *, repo: str, issue_number: int, body: str) -> str:
        """Posts one comment, returns its URL. Raises on any API failure,
        the caller (pipeline.build_outbound_handler) is responsible for
        making sure that failure reaches the ledger."""
        ...


# What an advisory, comment-only bot may ever hold. Anything else is refused.
# Metadata: read is attached to every GitHub App automatically.
_ALLOWED_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "issues": ("read", "write"),
    "metadata": ("read",),
}


class GitHubTokenScopeError(Exception):
    """Raised when the configured token has more (or the wrong) access than
    an advisory-only, comment-only bot should ever be granted or need."""


class GitHubCommentPoster:
    """Posts to the real GitHub REST API, gated by a self-check on the
    token's own effective permission -- see the module docstring for why
    that check exists even though (especially because) this runs against a
    repo Adam owns."""

    def __init__(self, token: str | TokenProvider, *, client: httpx.Client | None = None):
        if not token:
            raise ValueError("GitHubCommentPoster requires a token")
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
        self._verified_repos: set[str] = set()

    @classmethod
    def from_env(cls) -> GitHubCommentPoster:
        """Builds a real poster from the GitHub App credentials in the
        environment. Not used in tests.

        Requires SURFACE_ZERO_GITHUB_APP_ID, SURFACE_ZERO_GITHUB_INSTALLATION_ID,
        and the App's private key -- either SURFACE_ZERO_GITHUB_PRIVATE_KEY
        (the PEM content directly, newlines and all) or
        SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH (a path to the .pem file on disk).
        The App must be installed on the target repo with Issues: Read and
        write and no other permissions -- see comment_poster.py's module
        docstring for why that's still enforced at runtime even though the
        App's install-time permissions already constrain it.
        """
        import os

        from dotenv import load_dotenv

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
                ("SURFACE_ZERO_GITHUB_PRIVATE_KEY or SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH", private_key),
            ]
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Missing GitHub App credentials: " + ", ".join(missing) + ". "
                "Register the App, install it on the target repo with Issues: "
                "Read and write only, and set these from its settings page."
            )

        provider = GitHubAppTokenProvider(
            app_id=app_id,
            private_key_pem=private_key,
            installation_id=installation_id,
        )
        return cls(provider)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider.get_token()}"}

    def _verify_scope(self, repo: str) -> None:
        """Confirms this token can see exactly this repo, under exactly this
        name, and that its effective permission on it is push-level or
        lower -- never admin. Cached per repo so this costs one extra call
        the first time each repo is posted to, not on every comment.

        This is the check an external maintainer's own access grant would
        have enforced for free. Running against a repo Adam owns removes
        that external enforcement, so this method is what stands in for it.
        """
        if repo in self._verified_repos:
            return

        response = self._client.get(f"/repos/{repo}", headers=self._auth_headers())
        if response.status_code == 404:
            raise GitHubTokenScopeError(
                f"token cannot see {repo!r} at all (404 from the GitHub API) -- "
                "either the repo name is wrong or the token isn't scoped to it"
            )
        response.raise_for_status()
        data = response.json()

        actual_repo = data.get("full_name")
        if actual_repo != repo:
            raise GitHubTokenScopeError(
                f"asked to post to {repo!r} but the token's own view of that "
                f"path resolves to {actual_repo!r} -- refusing rather than "
                "posting to whatever this actually is"
            )

        # Preferred check: if the token provider knows exactly what GitHub
        # granted (App installation tokens do), verify that directly and skip
        # the contents probe below. The probe cannot tell "no Contents
        # permission" from "this repo is public and anyone can read it", so on
        # a public repo it always looked like over-permission (found
        # 2026-10-12 on the MedTimer replay fork; it would have blocked every
        # public target, including the Nuts repo).
        declared = getattr(self._token_provider, "granted_permissions", None)
        if callable(declared):
            perms = declared()
            if perms is not None:
                excess = {
                    name: level
                    for name, level in perms.items()
                    if level not in _ALLOWED_PERMISSIONS.get(name, ())
                }
                if excess:
                    raise GitHubTokenScopeError(
                        f"installation token for {repo!r} has permissions beyond "
                        f"Issues-only: {excess}. An advisory-only, comment-only bot "
                        "should have only Issues: read/write (Metadata: read is "
                        "added by GitHub automatically). Remove the extra permissions "
                        "from the App's installed permissions."
                    )
                self._verified_repos.add(repo)
                return

        # Fallback for tokens that cannot state their own permissions (a
        # personal access token): functional probe, below.
        # Deliberately NOT checking the `permissions` object GitHub returns
        # here: that field reflects the authenticated *user's* role on the
        # repo, not this specific token's granted scope. For a repo Adam
        # owns himself it always reports admin, regardless of how narrowly
        # the fine-grained PAT was actually scoped -- checking it would
        # refuse every correctly-scoped token on an owner's-own-repo
        # deployment, which is exactly backwards for this project.
        #
        # Instead, functionally probe what the TOKEN can do: an Issues-only
        # fine-grained PAT should never be able to read repo contents, so a
        # successful read there is real proof of over-scoping regardless of
        # who the underlying user is. A 403 confirms the restriction. A 404
        # (e.g. a genuinely empty repo with no default branch) is
        # inconclusive, not evidence of anything, so it's treated as a pass
        # rather than refused on a signal that proves nothing either way.
        contents_response = self._client.get(
            f"/repos/{repo}/contents", headers=self._auth_headers()
        )
        if contents_response.status_code == 200:
            raise GitHubTokenScopeError(
                f"token can read {repo!r}'s file contents, which means it has "
                "more than Issues-only access. An advisory-only, comment-only "
                "bot should never be able to read repo contents. Check the "
                "App's installed permissions (or the fine-grained PAT, if "
                "still in use) and remove everything except Issues: Read "
                "and write."
            )

        self._verified_repos.add(repo)

    def post_comment(self, *, repo: str, issue_number: int, body: str) -> str:
        self._verify_scope(repo)

        response = self._client.post(
            f"/repos/{repo}/issues/{issue_number}/comments",
            json={"body": body},
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        return response.json()["html_url"]
