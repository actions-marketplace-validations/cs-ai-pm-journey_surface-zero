import httpx
import pytest

from surface_zero.candidate_fetcher import GitHubCandidateFetcher

REPO = "ForgeAS/everForge"


def fetcher_with(handler) -> GitHubCandidateFetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    return GitHubCandidateFetcher("fake-token", client=client)


def test_missing_token_is_rejected_immediately():
    with pytest.raises(ValueError):
        GitHubCandidateFetcher("")


def test_fetches_open_and_closed_issues_excluding_self_and_prs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/{REPO}/issues"
        assert request.url.params["state"] == "all"
        assert request.url.params["direction"] == "asc"
        return httpx.Response(
            200,
            json=[
                {
                    "number": 39,
                    "title": "Custom tags disappeared",
                    "body": "tags gone",
                    "state": "closed",
                    "state_reason": "completed",
                },
                {
                    "number": 40,
                    "title": "Are custom tags broken?",
                    "body": "same thing?",
                    "state": "open",
                    "state_reason": None,
                },
                {"number": 41, "title": "the just-opened issue", "body": "...", "state": "open"},
                {
                    "number": 5,
                    "title": "a pull request, not an issue",
                    "body": "",
                    "state": "open",
                    "pull_request": {"url": "https://api.github.com/pulls/5"},
                },
            ],
        )

    fetcher = fetcher_with(handler)
    candidates = fetcher.fetch(repo=REPO, exclude_number=41)

    assert candidates == [
        {
            "number": 39,
            "title": "Custom tags disappeared",
            "body": "tags gone",
            "state": "closed",
            "state_reason": "completed",
        },
        {
            "number": 40,
            "title": "Are custom tags broken?",
            "body": "same thing?",
            "state": "open",
            "state_reason": None,
        },
    ]


def test_missing_body_becomes_empty_string_not_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"number": 1, "title": "no body field", "body": None, "state": "open"}]
        )

    fetcher = fetcher_with(handler)
    candidates = fetcher.fetch(repo=REPO, exclude_number=None)
    assert candidates == [
        {"number": 1, "title": "no body field", "body": "", "state": "open", "state_reason": None}
    ]


def test_missing_state_defaults_to_open():
    """Belt-and-suspenders against a GitHub response shape that omits state
    entirely -- should not happen in practice, but a missing field here must
    never silently read as closed."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"number": 2, "title": "no state field", "body": "x"}])

    fetcher = fetcher_with(handler)
    candidates = fetcher.fetch(repo=REPO, exclude_number=None)
    assert candidates[0]["state"] == "open"


def test_raises_on_api_error_so_the_caller_decides_how_to_degrade():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "GitHub is having a bad day"})

    fetcher = fetcher_with(handler)
    with pytest.raises(httpx.HTTPStatusError):
        fetcher.fetch(repo=REPO, exclude_number=None)

def test_paginates_through_every_open_issue_not_just_the_first_page():
    """Regression test for the 2026-09-29 finding: a single 100-item page
    silently dropped older issues -- the ones most likely to be a duplicate
    cluster's true original -- once a repo passed 100 open issues at once.
    fetch() must keep paging until GitHub returns a short (final) page."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        if page == 1:
            issues = [{"number": n, "title": f"issue {n}", "body": ""} for n in range(1, 101)]
        elif page == 2:
            issues = [{"number": n, "title": f"issue {n}", "body": ""} for n in range(101, 111)]
        else:
            issues = []
        return httpx.Response(200, json=issues)

    fetcher = fetcher_with(handler)
    candidates = fetcher.fetch(repo=REPO, exclude_number=None)

    assert calls == [1, 2]
    assert len(candidates) == 110
    assert candidates[0]["number"] == 1
    assert candidates[-1]["number"] == 110
