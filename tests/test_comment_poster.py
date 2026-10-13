import httpx
import pytest

from surface_zero.comment_poster import GitHubCommentPoster, GitHubTokenScopeError

REPO = "ForgeAS/everForge"


def _repo_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"full_name": REPO})


def poster_with(handler) -> GitHubCommentPoster:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    return GitHubCommentPoster("fake-token", client=client)


def test_missing_token_is_rejected_immediately():
    with pytest.raises(ValueError):
        GitHubCommentPoster("")


def test_posts_a_comment_when_scope_checks_out():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO})
        if request.url.path == f"/repos/{REPO}/contents":
            # Issues-only token: no Contents permission granted.
            return httpx.Response(403, json={"message": "Resource not accessible"})
        if request.url.path == f"/repos/{REPO}/issues/42/comments":
            assert request.method == "POST"
            return httpx.Response(
                201, json={"html_url": "https://github.com/ForgeAS/everForge/issues/42#comment"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    poster = poster_with(handler)
    url = poster.post_comment(repo=REPO, issue_number=42, body="Suggested label: **bug**")
    assert url == "https://github.com/ForgeAS/everForge/issues/42#comment"
    assert ("GET", f"/repos/{REPO}") in calls
    assert ("GET", f"/repos/{REPO}/contents") in calls
    assert ("POST", f"/repos/{REPO}/issues/42/comments") in calls


def test_an_owners_own_admin_role_does_not_trip_the_check():
    """The exact bug this file exists to prevent a regression of: GitHub's
    `permissions.admin` field reflects the authenticated user's role, which
    is always true for a repo Adam owns himself, regardless of how narrowly
    the fine-grained token was actually scoped. The check must not use that
    field -- a correctly-scoped token on your own repo must be accepted."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}":
            # Note: permissions.admin is True here on purpose, simulating
            # what the real API returns for a repo owner. Must be ignored.
            return httpx.Response(
                200, json={"full_name": REPO, "permissions": {"admin": True, "push": True}}
            )
        if request.url.path == f"/repos/{REPO}/contents":
            return httpx.Response(403, json={"message": "Resource not accessible"})
        return httpx.Response(
            201, json={"html_url": "https://github.com/ForgeAS/everForge/issues/1#comment"}
        )

    poster = poster_with(handler)
    url = poster.post_comment(repo=REPO, issue_number=1, body="hi")
    assert url == "https://github.com/ForgeAS/everForge/issues/1#comment"


def test_token_with_contents_access_is_refused_not_used():
    """The actual, corrected check: a token that can read repo contents has
    more than an advisory-only, comment-only bot should ever hold, whether
    or not Adam owns the repo it's pointed at."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO})
        if request.url.path == f"/repos/{REPO}/contents":
            return httpx.Response(200, json=[{"name": "README.md"}])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    poster = poster_with(handler)
    with pytest.raises(GitHubTokenScopeError, match="contents"):
        poster.post_comment(repo=REPO, issue_number=1, body="hi")


def test_an_empty_repo_contents_404_is_inconclusive_not_a_refusal():
    """A repo with no default branch/content returns 404 from the contents
    endpoint regardless of token scope -- that proves nothing either way,
    so it must not be treated as evidence of over-scoping."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO})
        if request.url.path == f"/repos/{REPO}/contents":
            return httpx.Response(404, json={"message": "This repository is empty."})
        return httpx.Response(
            201, json={"html_url": "https://github.com/ForgeAS/everForge/issues/1#comment"}
        )

    poster = poster_with(handler)
    url = poster.post_comment(repo=REPO, issue_number=1, body="hi")
    assert url == "https://github.com/ForgeAS/everForge/issues/1#comment"


def test_repo_name_mismatch_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"full_name": "someone-else/other-repo"})

    poster = poster_with(handler)
    with pytest.raises(GitHubTokenScopeError, match="resolves to"):
        poster.post_comment(repo=REPO, issue_number=1, body="hi")


def test_repo_not_visible_to_token_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    poster = poster_with(handler)
    with pytest.raises(GitHubTokenScopeError, match="404"):
        poster.post_comment(repo=REPO, issue_number=1, body="hi")


def test_scope_check_is_cached_not_repeated_per_comment():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO})
        if request.url.path == f"/repos/{REPO}/contents":
            return httpx.Response(403, json={"message": "Resource not accessible"})
        return httpx.Response(
            201, json={"html_url": "https://github.com/ForgeAS/everForge/issues/1#comment"}
        )

    poster = poster_with(handler)
    poster.post_comment(repo=REPO, issue_number=1, body="first")
    poster.post_comment(repo=REPO, issue_number=2, body="second")

    scope_checks = [c for c in calls if c[1] in (f"/repos/{REPO}", f"/repos/{REPO}/contents")]
    assert len(scope_checks) == 2  # one GET /repos + one GET /contents, only on the first call


# --- GitHub App auth (added 2026-09-24, migration off the personal PAT) ---

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from surface_zero.comment_poster import GitHubAppTokenProvider, StaticToken


def _generate_test_private_key_pem() -> str:
    """A throwaway RSA key, generated fresh per test run -- never the App's
    real key, which never appears in this codebase or in test fixtures."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("utf-8")


_TEST_PRIVATE_KEY_PEM = _generate_test_private_key_pem()


def test_static_token_just_returns_what_it_was_given():
    assert StaticToken("abc123").get_token() == "abc123"


def test_app_token_provider_requires_all_three_fields():
    with pytest.raises(ValueError):
        GitHubAppTokenProvider(app_id="", private_key_pem=_TEST_PRIVATE_KEY_PEM, installation_id="1")
    with pytest.raises(ValueError):
        GitHubAppTokenProvider(app_id="1", private_key_pem="", installation_id="1")
    with pytest.raises(ValueError):
        GitHubAppTokenProvider(app_id="1", private_key_pem=_TEST_PRIVATE_KEY_PEM, installation_id="")


def test_app_token_provider_mints_a_jwt_and_exchanges_it():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.url.path == "/app/installations/999/access_tokens"
        auth = request.headers["authorization"]
        assert auth.startswith("Bearer ")
        app_jwt = auth.removeprefix("Bearer ")
        # Decoded without verifying signature here: the point of this
        # assertion is that the *claims* are right (iss = this App's id,
        # exp in the future), not to re-implement GitHub's own JWT
        # verification.
        claims = pyjwt.decode(app_jwt, options={"verify_signature": False})
        assert claims["iss"] == "12345"
        assert claims["exp"] > claims["iat"]
        return httpx.Response(201, json={"token": "installation-token-abc", "expires_at": "2099-01-01T00:00:00Z"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    provider = GitHubAppTokenProvider(
        app_id="12345", private_key_pem=_TEST_PRIVATE_KEY_PEM, installation_id="999", client=client
    )

    assert provider.get_token() == "installation-token-abc"
    assert len(calls) == 1


def test_app_token_provider_caches_the_installation_token():
    exchange_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchange_count
        exchange_count += 1
        return httpx.Response(201, json={"token": f"token-{exchange_count}", "expires_at": "2099-01-01T00:00:00Z"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    provider = GitHubAppTokenProvider(
        app_id="12345", private_key_pem=_TEST_PRIVATE_KEY_PEM, installation_id="999", client=client
    )

    first = provider.get_token()
    second = provider.get_token()

    assert first == second == "token-1"
    assert exchange_count == 1


def test_app_token_provider_refreshes_once_the_cached_token_is_near_expiry(monkeypatch):
    exchange_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal exchange_count
        exchange_count += 1
        return httpx.Response(201, json={"token": f"token-{exchange_count}", "expires_at": "irrelevant"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    provider = GitHubAppTokenProvider(
        app_id="12345", private_key_pem=_TEST_PRIVATE_KEY_PEM, installation_id="999", client=client
    )

    fake_now = [1_000_000.0]
    monkeypatch.setattr("surface_zero.comment_poster.time.time", lambda: fake_now[0])

    first = provider.get_token()
    assert first == "token-1"
    assert exchange_count == 1

    # Still comfortably inside the 1-hour lifetime: no re-exchange.
    fake_now[0] += 10 * 60
    assert provider.get_token() == "token-1"
    assert exchange_count == 1

    # Past the refresh margin (55+ minutes in): must mint a new one.
    fake_now[0] += 50 * 60
    assert provider.get_token() == "token-2"
    assert exchange_count == 2


def test_comment_poster_accepts_a_token_provider_instead_of_a_bare_string():
    """GitHubCommentPoster's constructor must work with anything that has a
    get_token() method, not just a raw string -- that's the seam the App
    migration hangs off of without touching every call site."""
    get_calls = []

    class RecordingProvider:
        def get_token(self) -> str:
            get_calls.append(1)
            return "provider-token"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer provider-token"
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO})
        if request.url.path == f"/repos/{REPO}/contents":
            return httpx.Response(403, json={"message": "Resource not accessible"})
        return httpx.Response(
            201, json={"html_url": "https://github.com/ForgeAS/everForge/issues/1#comment"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    poster = GitHubCommentPoster(RecordingProvider(), client=client)
    poster.post_comment(repo=REPO, issue_number=1, body="hi")

    # Once per request (repo check, contents check, the comment POST) --
    # confirms the provider is consulted live rather than the token being
    # captured once at construction time, which matters once it can expire.
    assert len(get_calls) == 3


# --- Declared-permission check (added 2026-10-12): the contents probe false-positives on public repos ---


class _DeclaredPermsProvider:
    def __init__(self, perms):
        self._perms = perms

    def get_token(self) -> str:
        return "app-token"

    def granted_permissions(self):
        return self._perms


def _public_repo_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == f"/repos/{REPO}":
        return httpx.Response(200, json={"full_name": REPO})
    if request.url.path == f"/repos/{REPO}/contents":
        # A PUBLIC repo: contents are readable by anyone, whatever the token's permissions.
        return httpx.Response(200, json=[])
    if request.url.path == f"/repos/{REPO}/issues/7/comments":
        return httpx.Response(201, json={"html_url": "https://github.com/x/y/issues/7#comment"})
    raise AssertionError(f"unexpected request: {request.method} {request.url.path}")


def _poster_with_provider(provider, handler) -> GitHubCommentPoster:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    return GitHubCommentPoster(provider, client=client)


def test_issues_only_app_token_is_accepted_on_a_public_repo():
    """The regression: on a public repo the contents probe returns 200 for any
    token. An App token that DECLARES Issues-only must be accepted anyway."""
    poster = _poster_with_provider(
        _DeclaredPermsProvider({"issues": "write", "metadata": "read"}), _public_repo_handler
    )
    assert poster.post_comment(repo=REPO, issue_number=7, body="x").endswith("#comment")


def test_declared_permissions_skip_the_contents_probe_entirely():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _public_repo_handler(request)

    _poster_with_provider(
        _DeclaredPermsProvider({"issues": "write", "metadata": "read"}), handler
    ).post_comment(repo=REPO, issue_number=7, body="x")
    assert f"/repos/{REPO}/contents" not in seen


@pytest.mark.parametrize(
    "perms, name",
    [
        ({"issues": "write", "metadata": "read", "contents": "read"}, "contents"),
        ({"issues": "write", "metadata": "read", "pull_requests": "write"}, "pull_requests"),
        ({"issues": "admin", "metadata": "read"}, "issues"),
        ({"issues": "write", "metadata": "write"}, "metadata"),
    ],
)
def test_app_token_with_any_extra_or_stronger_permission_is_refused(perms, name):
    poster = _poster_with_provider(_DeclaredPermsProvider(perms), _public_repo_handler)
    with pytest.raises(GitHubTokenScopeError, match=name):
        poster.post_comment(repo=REPO, issue_number=7, body="x")


def test_provider_without_declared_permissions_still_falls_back_to_the_probe():
    poster = _poster_with_provider(_DeclaredPermsProvider(None), _public_repo_handler)
    with pytest.raises(GitHubTokenScopeError, match="contents"):
        poster.post_comment(repo=REPO, issue_number=7, body="x")


def test_app_token_provider_records_the_permissions_github_reports():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"token": "t", "expires_at": "2099-01-01T00:00:00Z",
                  "permissions": {"issues": "write", "metadata": "read"}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    provider = GitHubAppTokenProvider(
        app_id="1", private_key_pem=_TEST_PRIVATE_KEY_PEM, installation_id="9", client=client
    )
    assert provider.granted_permissions() == {"issues": "write", "metadata": "read"}


def test_app_token_provider_reports_none_when_github_omits_permissions():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"token": "t", "expires_at": "2099-01-01T00:00:00Z"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    provider = GitHubAppTokenProvider(
        app_id="1", private_key_pem=_TEST_PRIVATE_KEY_PEM, installation_id="9", client=client
    )
    assert provider.granted_permissions() is None


# --- scope_policy="workflow" (GitHub Action; the workflow token cannot state its permissions) ---


def test_workflow_policy_accepts_a_public_repo_and_never_runs_the_contents_probe():
    """The probe wrongly refuses every public repo (anyone can read public
    contents). In the Action the token is the workflow's GITHUB_TOKEN, so the
    permission half of the check is skipped and only repo identity is verified."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO})
        if request.url.path == f"/repos/{REPO}/contents":
            return httpx.Response(200, json=[])  # public repo: readable by anyone
        if request.url.path == f"/repos/{REPO}/issues/1/comments":
            return httpx.Response(201, json={"html_url": "https://example/c"})
        raise AssertionError(request.url.path)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    poster = GitHubCommentPoster("t", client=client, scope_policy="workflow")
    assert poster.post_comment(repo=REPO, issue_number=1, body="x") == "https://example/c"
    assert ("GET", f"/repos/{REPO}/contents") not in calls


def test_workflow_policy_still_refuses_a_repo_identity_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"full_name": "someone/else"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    poster = GitHubCommentPoster("t", client=client, scope_policy="workflow")
    with pytest.raises(GitHubTokenScopeError):
        poster.post_comment(repo=REPO, issue_number=1, body="x")


def test_unknown_scope_policy_is_rejected():
    with pytest.raises(ValueError):
        GitHubCommentPoster("t", scope_policy="whatever")
