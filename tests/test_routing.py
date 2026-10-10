import pytest

from surface_zero.routing import (
    InstallationConfigError,
    RoutingCandidateFetcher,
    RoutingCommentPoster,
    UnroutedRepoError,
    build_routed_clients,
    parse_repo_installations,
)


class Recorder:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def post_comment(self, *, repo, issue_number, body):
        self.calls.append((repo, issue_number, body))
        return f"https://x/{self.name}"

    def fetch(self, *, repo, exclude_number):
        self.calls.append((repo, exclude_number))
        return [{"number": 1, "from": self.name}]


def test_parse_blank_or_unset_means_single_installation_path():
    assert parse_repo_installations(None) is None
    assert parse_repo_installations("   ") is None
    assert parse_repo_installations(" , ") is None


def test_parse_valid_mapping_lowercases_repo_names():
    assert parse_repo_installations("ForgeAS/everForge=111, thevpc/nuts=222") == {
        "forgeas/everforge": "111",
        "thevpc/nuts": "222",
    }


@pytest.mark.parametrize(
    "raw",
    ["noequals", "repo-without-slash=1", "a/b=notanumber", "a/b=", "a/b=1,A/B=2"],
)
def test_parse_rejects_malformed_entries_instead_of_skipping(raw):
    with pytest.raises(InstallationConfigError):
        parse_repo_installations(raw)


def test_poster_routes_by_repo_case_insensitively():
    a, b = Recorder("a"), Recorder("b")
    router = RoutingCommentPoster({"o/one": a, "o/two": b})
    assert router.post_comment(repo="O/Two", issue_number=5, body="x") == "https://x/b"
    assert a.calls == [] and len(b.calls) == 1


def test_poster_unrouted_repo_raises_and_calls_nobody():
    a = Recorder("a")
    router = RoutingCommentPoster({"o/one": a})
    with pytest.raises(UnroutedRepoError):
        router.post_comment(repo="o/other", issue_number=1, body="x")
    assert a.calls == []


def test_fetcher_routes_and_rejects_unrouted():
    a, b = Recorder("a"), Recorder("b")
    router = RoutingCandidateFetcher({"o/one": a, "o/two": b})
    assert router.fetch(repo="o/one", exclude_number=3) == [{"number": 1, "from": "a"}]
    with pytest.raises(UnroutedRepoError):
        router.fetch(repo="o/none", exclude_number=3)
    assert b.calls == []


def test_build_routed_clients_shares_one_token_provider_per_installation():
    poster, fetcher = build_routed_clients(
        app_id="1",
        private_key_pem="not-a-real-key",
        repo_installations={"o/one": "111", "o/two": "111", "o/three": "222"},
    )
    p1 = poster._posters["o/one"]._token_provider
    p2 = poster._posters["o/two"]._token_provider
    p3 = poster._posters["o/three"]._token_provider
    assert p1 is p2
    assert p1 is not p3
    assert fetcher._fetchers["o/one"]._token_provider is p1
