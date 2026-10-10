import hashlib
import hmac

import pytest

from surface_zero.webhook import (
    MalformedEventError,
    SignatureError,
    parse_event,
    to_run_request,
    verify_signature,
)
from tests.fixtures.github_payloads import (
    as_bytes,
    issues_labeled_payload,
    issues_opened_payload,
    pull_request_opened_payload,
)

SECRET = "test-webhook-secret"


def sign(payload_bytes: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# -- signature verification ---------------------------------------------------


def test_valid_signature_passes():
    body = as_bytes(issues_opened_payload())
    verify_signature(SECRET, body, sign(body))  # does not raise


def test_wrong_secret_is_rejected():
    body = as_bytes(issues_opened_payload())
    with pytest.raises(SignatureError):
        verify_signature(SECRET, body, sign(body, secret="wrong-secret"))


def test_tampered_body_is_rejected():
    body = as_bytes(issues_opened_payload())
    signature = sign(body)
    tampered = as_bytes(issues_opened_payload(title="a different title"))
    with pytest.raises(SignatureError):
        verify_signature(SECRET, tampered, signature)


def test_missing_signature_header_is_rejected():
    body = as_bytes(issues_opened_payload())
    with pytest.raises(SignatureError):
        verify_signature(SECRET, body, None)


def test_malformed_signature_header_is_rejected():
    body = as_bytes(issues_opened_payload())
    with pytest.raises(SignatureError):
        verify_signature(SECRET, body, "not-the-right-format")


# -- event classification -----------------------------------------------------


def test_issues_opened_by_a_person_should_process():
    body = issues_opened_payload(author="a-real-person")
    event = parse_event(event_header="issues", delivery_id="d1", payload_bytes=as_bytes(body))
    assert event.should_process is True
    assert event.repo == "fixture/example-repo"
    assert event.issue_number == 42

    request = to_run_request(event)
    assert request.idempotency_key == "d1"
    assert request.target == "fixture/example-repo"
    assert request.payload["number"] == 42


def test_pull_request_events_are_ignored():
    event = parse_event(
        event_header="pull_request",
        delivery_id="d2",
        payload_bytes=as_bytes(pull_request_opened_payload()),
    )
    assert event.should_process is False
    assert "out of scope" in event.ignore_reason


def test_issue_labeled_action_is_ignored_not_reprocessed():
    event = parse_event(
        event_header="issues", delivery_id="d3", payload_bytes=as_bytes(issues_labeled_payload())
    )
    assert event.should_process is False
    assert "opened only" in event.ignore_reason


def test_bot_authored_issue_is_ignored():
    event = parse_event(
        event_header="issues",
        delivery_id="d4",
        payload_bytes=as_bytes(issues_opened_payload(author="renovate[bot]")),
    )
    assert event.should_process is False
    assert "bot" in event.ignore_reason


def test_bot_login_match_is_case_insensitive():
    event = parse_event(
        event_header="issues",
        delivery_id="d5",
        payload_bytes=as_bytes(issues_opened_payload(author="Dependabot[bot]")),
    )
    assert event.should_process is False


# -- malformed events -----------------------------------------------------------


def test_missing_delivery_header_is_malformed():
    with pytest.raises(MalformedEventError, match="Delivery"):
        parse_event(
            event_header="issues", delivery_id=None, payload_bytes=as_bytes(issues_opened_payload())
        )


def test_missing_event_header_is_malformed():
    with pytest.raises(MalformedEventError, match="GitHub-Event"):
        parse_event(event_header=None, delivery_id="d6", payload_bytes=as_bytes(issues_opened_payload()))


def test_invalid_json_body_is_malformed():
    with pytest.raises(MalformedEventError, match="not valid JSON"):
        parse_event(event_header="issues", delivery_id="d7", payload_bytes=b"{not json")


def test_issues_event_missing_issue_object_is_malformed():
    with pytest.raises(MalformedEventError, match="issue"):
        parse_event(
            event_header="issues",
            delivery_id="d8",
            payload_bytes=as_bytes({"action": "opened", "repository": {"full_name": "x/y"}}),
        )


# -- ignored events still reach the ledger -------------------------------------


from surface_zero.ledger import Ledger, OutboundAction, RunStatus  # noqa: E402
from surface_zero.webhook import record_ignored_event  # noqa: E402


def test_ignored_event_is_recorded_not_dropped(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    event = parse_event(
        event_header="pull_request", delivery_id="pr-1", payload_bytes=as_bytes(pull_request_opened_payload())
    )
    record, created = record_ignored_event(ledger, event)
    assert created is True
    assert record.status == RunStatus.IGNORED
    assert record.outbound_action == OutboundAction.NONE
    assert "out of scope" in record.response["ignore_reason"]


def test_ignored_is_distinct_from_abstained(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    event = parse_event(
        event_header="issues", delivery_id="bot-1", payload_bytes=as_bytes(issues_opened_payload(author="dependabot[bot]"))
    )
    record, _ = record_ignored_event(ledger, event)
    assert record.status == RunStatus.IGNORED
    assert record.status != RunStatus.ABSTAINED


def test_duplicate_ignored_delivery_does_not_write_twice(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    event = parse_event(
        event_header="issues", delivery_id="dup-ignored", payload_bytes=as_bytes(issues_labeled_payload())
    )
    first, first_created = record_ignored_event(ledger, event)
    second, second_created = record_ignored_event(ledger, event)
    assert first_created is True
    assert second_created is False
    assert first.run_id == second.run_id
    assert len(ledger.list_runs()) == 1


def test_author_association_is_recorded_in_the_run_payload_not_used_to_skip():
    from surface_zero.webhook import parse_event, to_run_request
    from tests.fixtures.github_payloads import as_bytes, issues_opened_payload

    payload = issues_opened_payload()
    payload["issue"]["author_association"] = "OWNER"
    event = parse_event(event_header="issues", delivery_id="aa1", payload_bytes=as_bytes(payload))
    assert event.should_process is True
    assert to_run_request(event).payload["author_association"] == "OWNER"


def test_missing_author_association_is_none_not_an_error():
    from surface_zero.webhook import parse_event, to_run_request
    from tests.fixtures.github_payloads import as_bytes, issues_opened_payload

    event = parse_event(event_header="issues", delivery_id="aa2", payload_bytes=as_bytes(issues_opened_payload()))
    assert to_run_request(event).payload["author_association"] is None
