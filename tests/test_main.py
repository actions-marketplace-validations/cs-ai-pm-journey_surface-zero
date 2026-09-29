import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from surface_zero.killswitch import KillSwitch
from surface_zero.ledger import Ledger, OutboundAction, RunStatus
from surface_zero.main import create_app
from surface_zero.runner import AgentResult
from surface_zero.webhook import DELIVERY_HEADER, EVENT_HEADER, SIGNATURE_HEADER
from tests.fake_anthropic import FakeAnthropic
from tests.fixtures.github_payloads import (
    as_bytes,
    issues_labeled_payload,
    issues_opened_payload,
    pull_request_opened_payload,
)
from tests.test_pipeline import FakePoster

SECRET = "test-webhook-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def label_agent(payload, ctx):
    return AgentResult(response={"suggested_label": "bug", "rationale": "crashes"})


@pytest.fixture
def app_parts(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    kill_switch = KillSwitch(tmp_path / "kill.json")
    poster = FakePoster()
    app = create_app(
        webhook_secret=SECRET,
        ledger=ledger,
        kill_switch=kill_switch,
        agent=label_agent,
        poster=poster,
    )
    return TestClient(app), ledger, kill_switch, poster


def post_event(client, *, event, delivery, payload, secret=SECRET):
    body = as_bytes(payload)
    return client.post(
        "/webhook",
        content=body,
        headers={
            SIGNATURE_HEADER: sign(body, secret),
            EVENT_HEADER: event,
            DELIVERY_HEADER: delivery,
            "content-type": "application/json",
        },
    )


def test_valid_opened_issue_is_processed_end_to_end(app_parts):
    client, ledger, kill_switch, poster = app_parts
    resp = post_event(client, event="issues", delivery="d1", payload=issues_opened_payload())
    assert resp.status_code == 200
    assert len(poster.calls) == 1
    (run,) = ledger.list_runs()
    assert run.status == RunStatus.SUCCEEDED
    assert run.outbound_action == OutboundAction.COMMENT_POSTED


def test_bad_signature_is_401_and_writes_no_ledger_row(app_parts):
    client, ledger, _, poster = app_parts
    resp = post_event(client, event="issues", delivery="d2", payload=issues_opened_payload(), secret="wrong")
    assert resp.status_code == 401
    assert ledger.list_runs() == []
    assert poster.calls == []


def test_malformed_body_is_400(app_parts):
    client, ledger, _, _ = app_parts
    body = b"{not valid json"
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            SIGNATURE_HEADER: sign(body),
            EVENT_HEADER: "issues",
            DELIVERY_HEADER: "d3",
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 400
    assert ledger.list_runs() == []


def test_pull_request_event_is_200_and_ignored_not_processed(app_parts):
    client, ledger, _, poster = app_parts
    resp = post_event(client, event="pull_request", delivery="d4", payload=pull_request_opened_payload())
    assert resp.status_code == 200
    assert poster.calls == []
    (run,) = ledger.list_runs()
    assert run.status == RunStatus.IGNORED


def test_issue_labeled_action_is_ignored(app_parts):
    client, ledger, _, poster = app_parts
    resp = post_event(client, event="issues", delivery="d5", payload=issues_labeled_payload())
    assert resp.status_code == 200
    (run,) = ledger.list_runs()
    assert run.status == RunStatus.IGNORED
    assert poster.calls == []


def test_redelivery_with_same_delivery_id_never_posts_twice(app_parts):
    client, ledger, _, poster = app_parts
    payload = issues_opened_payload()
    r1 = post_event(client, event="issues", delivery="same-id", payload=payload)
    r2 = post_event(client, event="issues", delivery="same-id", payload=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(poster.calls) == 1
    assert len(ledger.list_runs()) == 1


def test_kill_switch_active_returns_200_but_never_posts(app_parts):
    client, ledger, kill_switch, poster = app_parts
    kill_switch.engage("testing the endpoint")
    resp = post_event(client, event="issues", delivery="d6", payload=issues_opened_payload())
    assert resp.status_code == 200
    assert poster.calls == []
    (run,) = ledger.list_runs()
    assert run.outbound_action == OutboundAction.COMMENT_SUPPRESSED


def test_missing_signature_header_is_401(app_parts):
    client, ledger, _, _ = app_parts
    body = as_bytes(issues_opened_payload())
    resp = client.post(
        "/webhook",
        content=body,
        headers={EVENT_HEADER: "issues", DELIVERY_HEADER: "d7", "content-type": "application/json"},
    )
    assert resp.status_code == 401
    assert ledger.list_runs() == []


class FakeCandidateFetcher:
    """Records what it was called with; returns a fixed candidate list."""

    def __init__(self, candidates=None, error=None):
        self.calls = []
        self._candidates = candidates if candidates is not None else []
        self._error = error

    def fetch(self, *, repo, exclude_number):
        self.calls.append((repo, exclude_number))
        if self._error:
            raise self._error
        return self._candidates


def test_candidate_fetcher_result_reaches_the_agent(tmp_path):
    """The whole point of this block's Monday fix: candidates fetched from
    GitHub must actually reach the agent's payload, not just get fetched
    and discarded."""
    seen_payloads = []

    def capturing_agent(payload, ctx):
        seen_payloads.append(payload)
        return AgentResult(response={"suggested_label": "bug", "rationale": "crashes"})

    fetcher = FakeCandidateFetcher(candidates=[{"number": 1, "title": "t", "body": "b"}])
    ledger = Ledger(tmp_path / "l.db")
    kill_switch = KillSwitch(tmp_path / "kill.json")
    poster = FakePoster()
    app = create_app(
        webhook_secret=SECRET,
        ledger=ledger,
        kill_switch=kill_switch,
        agent=capturing_agent,
        poster=poster,
        candidate_fetcher=fetcher,
    )
    client = TestClient(app)
    resp = post_event(client, event="issues", delivery="cand-1", payload=issues_opened_payload())

    assert resp.status_code == 200
    assert len(fetcher.calls) == 1
    assert seen_payloads[0]["candidates"] == [{"number": 1, "title": "t", "body": "b"}]


def test_candidate_fetcher_failure_never_blocks_the_run(tmp_path):
    """A GitHub API hiccup while fetching candidates must degrade to no
    candidates, never take down the whole webhook -- same discipline as
    every other outbound call in this file."""
    fetcher = FakeCandidateFetcher(error=RuntimeError("GitHub is down"))
    ledger = Ledger(tmp_path / "l.db")
    kill_switch = KillSwitch(tmp_path / "kill.json")
    poster = FakePoster()
    app = create_app(
        webhook_secret=SECRET,
        ledger=ledger,
        kill_switch=kill_switch,
        agent=label_agent,
        poster=poster,
        candidate_fetcher=fetcher,
    )
    client = TestClient(app)
    resp = post_event(client, event="issues", delivery="cand-2", payload=issues_opened_payload())

    assert resp.status_code == 200
    assert len(poster.calls) == 1
    (run,) = ledger.list_runs()
    assert run.status == RunStatus.SUCCEEDED


def test_no_candidate_fetcher_configured_behaves_exactly_as_before(app_parts):
    """create_app's default (candidate_fetcher=None) must reproduce every
    existing behavior exactly -- this parameter is additive, not a change
    to any run that doesn't opt into it."""
    client, ledger, _, poster = app_parts
    resp = post_event(client, event="issues", delivery="cand-3", payload=issues_opened_payload())
    assert resp.status_code == 200
    assert len(poster.calls) == 1
