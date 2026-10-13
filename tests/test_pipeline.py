import pytest

from surface_zero.killswitch import KillSwitch
from surface_zero.ledger import Ledger, OutboundAction, RunStatus
from surface_zero.pipeline import build_outbound_handler, render_comment
from surface_zero.runner import RunRequest, run_request


class FakePoster:
    def __init__(self, url: str = "https://github.com/x/y/issues/42#comment", error: Exception | None = None):
        self.url = url
        self.error = error
        self.calls: list[dict] = []

    def post_comment(self, *, repo, issue_number, body):
        self.calls.append({"repo": repo, "issue_number": issue_number, "body": body})
        if self.error is not None:
            raise self.error
        return self.url


def label_agent(payload, ctx):
    from surface_zero.runner import AgentResult

    return AgentResult(response={"suggested_label": "bug", "rationale": "crashes on launch"})


def abstain_agent(payload, ctx):
    from surface_zero.runner import AgentResult

    return AgentResult(response={"reason": "unclear"}, abstained=True)


def request(key="issue-1", number=42):
    return RunRequest(
        idempotency_key=key,
        event_type="issues.opened",
        target="fixture/example-repo",
        payload={"title": "App crashes", "body": "every time", "number": number},
    )


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.db")


@pytest.fixture
def kill_switch(tmp_path):
    return KillSwitch(tmp_path / "kill_switch.json")


def test_kill_switch_inactive_posts_the_comment(ledger, kill_switch):
    poster = FakePoster()
    handler = build_outbound_handler(poster, kill_switch)
    outcome = run_request(request(), label_agent, ledger, outbound=handler)

    assert len(poster.calls) == 1
    assert poster.calls[0]["repo"] == "fixture/example-repo"
    assert poster.calls[0]["issue_number"] == 42
    assert "bug" in poster.calls[0]["body"]
    assert outcome.run.status == RunStatus.SUCCEEDED
    assert outcome.run.outbound_action == OutboundAction.COMMENT_POSTED
    assert outcome.run.response["comment_url"] == poster.url


def test_kill_switch_active_never_calls_the_poster(ledger, kill_switch):
    kill_switch.engage("maintainer asked to pause")
    poster = FakePoster()
    handler = build_outbound_handler(poster, kill_switch)
    outcome = run_request(request(), label_agent, ledger, outbound=handler)

    assert poster.calls == []  # this is the actual assertion that matters
    assert outcome.run.status == RunStatus.SUCCEEDED  # the advisor still worked
    assert outcome.run.outbound_action == OutboundAction.COMMENT_SUPPRESSED
    assert outcome.run.response["kill_switch_reason"] == "maintainer asked to pause"


def test_abstained_runs_never_reach_the_poster(ledger, kill_switch):
    poster = FakePoster()
    handler = build_outbound_handler(poster, kill_switch)
    outcome = run_request(request(), abstain_agent, ledger, outbound=handler)

    assert poster.calls == []
    assert outcome.run.status == RunStatus.ABSTAINED
    assert outcome.run.outbound_action == OutboundAction.NONE


def test_github_api_failure_during_posting_is_recorded_as_failed(ledger, kill_switch):
    poster = FakePoster(error=RuntimeError("GitHub API returned 503"))
    handler = build_outbound_handler(poster, kill_switch)
    outcome = run_request(request(), label_agent, ledger, outbound=handler)

    assert len(poster.calls) == 1  # it did try
    assert outcome.run.status == RunStatus.FAILED
    assert outcome.run.error_type == "RuntimeError"
    assert outcome.run.outbound_action == OutboundAction.NONE


def test_duplicate_delivery_after_a_successful_post_never_posts_again(ledger, kill_switch):
    """This is the retry-storm guard the Wednesday brief calls out by name:
    a redelivered webhook (same delivery id) must never produce a second
    comment, whatever happened to the first attempt."""
    poster = FakePoster()
    handler = build_outbound_handler(poster, kill_switch)
    run_request(request("dup"), label_agent, ledger, outbound=handler)
    run_request(request("dup"), label_agent, ledger, outbound=handler)
    assert len(poster.calls) == 1


def test_duplicate_delivery_after_a_failed_post_never_retries_automatically(ledger, kill_switch):
    """Same guard on the failure path: an idempotency-key redelivery of a run
    whose comment failed to post does not get auto-retried, that's the
    orphan-handling call from Monday, a human decides, not the transport."""
    poster = FakePoster(error=RuntimeError("503"))
    handler = build_outbound_handler(poster, kill_switch)
    run_request(request("dup2"), label_agent, ledger, outbound=handler)
    run_request(request("dup2"), label_agent, ledger, outbound=handler)
    assert len(poster.calls) == 1


def test_flipping_the_kill_switch_mid_session_is_honored_on_the_next_run(ledger, kill_switch):
    poster = FakePoster()
    handler = build_outbound_handler(poster, kill_switch)
    run_request(request("a"), label_agent, ledger, outbound=handler)
    assert len(poster.calls) == 1

    kill_switch.engage("flip")
    run_request(request("b"), label_agent, ledger, outbound=handler)
    assert len(poster.calls) == 1  # still 1, the second run was suppressed


def test_render_comment_with_no_duplicate_is_unchanged():
    body = render_comment("bug", "Crashes on launch.")
    assert "Possible duplicate" not in body
    assert "Suggested label: **bug**" in body


def test_render_comment_open_duplicate_uses_the_original_plain_phrasing():
    """Regression guard: adding closed-issue handling must not change the
    wording for the far more common open-duplicate case."""
    body = render_comment(
        "bug", "Crashes on launch.", duplicate_of=3,
        duplicate_rationale="Same crash.", duplicate_of_state="open",
    )
    assert "Possible duplicate of #3: Same crash." in body
    assert "closed" not in body.lower()


def test_render_comment_closed_duplicate_flags_a_possible_regression():
    """The whole point of the 2026-09-29 option-A fix: a match against a
    closed issue must read differently -- and more visibly -- than a match
    against an open one, since it may mean something already-fixed broke
    again."""
    body = render_comment(
        "bug", "Crashes on launch.", duplicate_of=3,
        duplicate_rationale="Same crash as the closed report.",
        duplicate_of_state="closed", duplicate_of_state_reason="completed",
    )
    assert "closed **#3**" not in body  # sanity: number isn't bolded, "closed" is placed before it
    assert "closed** #3" in body
    assert "closed as completed" in body
    assert "regression" in body.lower()


def test_render_comment_closed_duplicate_with_unknown_reason_degrades_gracefully():
    body = render_comment(
        "bug", "x", duplicate_of=5, duplicate_rationale="y",
        duplicate_of_state="closed", duplicate_of_state_reason=None,
    )
    assert "closed** #5" in body
    assert "closed as None" not in body


def test_shadow_repo_runs_the_agent_but_never_calls_the_poster(ledger, kill_switch):
    poster = FakePoster()
    handler = build_outbound_handler(poster, kill_switch, shadow_repos=frozenset({"Fixture/Example-Repo"}))
    run_request(request(), label_agent, ledger, outbound=handler)

    assert poster.calls == []
    (run,) = ledger.list_runs()
    assert run.status == RunStatus.SUCCEEDED
    assert run.outbound_action == OutboundAction.SHADOW_RECORDED


def test_shadow_is_reported_as_shadow_not_as_a_kill_switch_suppression(ledger, kill_switch):
    kill_switch.engage("test")
    poster = FakePoster()
    handler = build_outbound_handler(poster, kill_switch, shadow_repos=frozenset({"fixture/example-repo"}))
    run_request(request(), label_agent, ledger, outbound=handler)

    (run,) = ledger.list_runs()
    assert run.outbound_action == OutboundAction.SHADOW_RECORDED


def test_non_shadow_repo_still_posts_when_another_repo_is_shadowed(ledger, kill_switch):
    poster = FakePoster()
    handler = build_outbound_handler(poster, kill_switch, shadow_repos=frozenset({"someone/else"}))
    run_request(request(), label_agent, ledger, outbound=handler)

    assert len(poster.calls) == 1


def test_rendered_comment_defuses_mentions_and_foreign_references_in_model_text():
    body = render_comment(
        "bug",
        "Reported by @someone, copy of original Futsch1#544.",
        duplicate_of=5,
        duplicate_rationale="Same as other/repo#9 per @maintainer.",
        repo="fixture/example-repo",
    )
    assert "`@someone`" in body and "`Futsch1#544`" in body
    assert "`other/repo#9`" in body and "`@maintainer`" in body
    assert "Possible duplicate of #5:" in body  # our own structured reference stays a real link


def test_rendered_comment_keeps_own_repo_references_as_links():
    body = render_comment("bug", "See #12 and fixture/example-repo#13.", repo="fixture/example-repo")
    assert "See #12 and fixture/example-repo#13." in body


def test_a_custom_footer_replaces_the_default_one():
    body = render_comment("bug", "It crashes.", footer="_Custom footer._")
    assert body.rstrip().endswith("_Custom footer._")
    assert "stop bot" not in body


def test_the_default_footer_is_unchanged_for_the_hosted_service():
    body = render_comment("bug", "It crashes.")
    assert "comment `stop bot` any time" in body
