"""GitHub webhook ingestion: signature verification, then classification.

Two separate concerns, deliberately kept apart:

* verify_signature answers "did this request actually come from GitHub." A
  request that fails this check is not treated as an event at all, it never
  reaches the ledger, because we have no basis for believing GitHub sent it.
  This is transport authentication, not business logic.
* classify_event answers "does Surface Zero act on this." Everything that
  passes signature verification DID come from GitHub and gets a ledger row,
  even when the answer is "ignore it", because ledger-first means every
  legitimate delivery is evidence, whether or not it's actionable.

Surface Zero only ever acts on a newly opened issue. Every other GitHub event
type, every other issue action (labeled, closed, edited, ...), and issues
opened by a known bot account are classified IGNORED and never reach the
advisor. This mirrors the authority scope in the Block 11 design docs: reads
new issues, doesn't touch existing issues or pull requests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from surface_zero.ledger import Ledger, OutboundAction, RunRecord, RunStatus
from surface_zero.runner import RunRequest

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

# GitHub's own bot accounts and common automation bots that open issues as a
# side effect of their own job (dependency dashboards, release notes, ...).
# Not what "new issue from a person" means, and never what a maintainer
# agreement's "new issues" language was written to cover. Extend this list
# per-target if a confirmed repo runs bots this doesn't already know about.
KNOWN_BOT_LOGINS = frozenset({"renovate", "renovate[bot]", "dependabot", "dependabot[bot]"})


class SignatureError(Exception):
    """The request's signature didn't verify. Not treated as a GitHub event."""


def verify_signature(secret: str, payload_bytes: bytes, signature_header: str | None) -> None:
    """Raises SignatureError unless signature_header is a valid HMAC-SHA256
    of payload_bytes using secret, in GitHub's `sha256=<hex>` format.

    Uses hmac.compare_digest specifically to avoid a timing side-channel, a
    naive `==` comparison leaks how many leading bytes matched.
    """
    if not signature_header or not signature_header.startswith("sha256="):
        raise SignatureError("missing or malformed X-Hub-Signature-256 header")
    provided = signature_header[len("sha256=") :]
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise SignatureError("signature does not match payload")


@dataclass(frozen=True)
class ParsedEvent:
    delivery_id: str
    event_type: str
    action: str | None
    repo: str | None
    should_process: bool
    ignore_reason: str | None
    issue_title: str | None
    issue_body: str | None
    issue_number: int | None


class MalformedEventError(Exception):
    """The request passed signature verification but the body isn't usable JSON,
    or is missing fields this parser depends on. Still a legitimate GitHub
    delivery, so the caller records this in the ledger rather than dropping it.
    """


def parse_event(
    *, event_header: str | None, delivery_id: str | None, payload_bytes: bytes
) -> ParsedEvent:
    if not delivery_id:
        raise MalformedEventError(f"missing {DELIVERY_HEADER} header")
    if not event_header:
        raise MalformedEventError(f"missing {EVENT_HEADER} header")

    try:
        body: dict[str, Any] = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise MalformedEventError(f"payload is not valid JSON: {exc}") from exc

    if event_header != "issues":
        return ParsedEvent(
            delivery_id=delivery_id,
            event_type=event_header,
            action=body.get("action"),
            repo=_repo_name(body),
            should_process=False,
            ignore_reason=f"event type {event_header!r} is out of scope, issues only",
            issue_title=None,
            issue_body=None,
            issue_number=None,
        )

    action = body.get("action")
    if action != "opened":
        return ParsedEvent(
            delivery_id=delivery_id,
            event_type=event_header,
            action=action,
            repo=_repo_name(body),
            should_process=False,
            ignore_reason=f"issue action {action!r} is out of scope, opened only",
            issue_title=None,
            issue_body=None,
            issue_number=None,
        )

    issue = body.get("issue")
    if not isinstance(issue, dict):
        raise MalformedEventError("issues.opened payload missing an 'issue' object")

    author = ((issue.get("user") or {}).get("login") or "").lower()
    if author in KNOWN_BOT_LOGINS:
        return ParsedEvent(
            delivery_id=delivery_id,
            event_type=event_header,
            action=action,
            repo=_repo_name(body),
            should_process=False,
            ignore_reason=f"issue opened by known bot account {author!r}",
            issue_title=None,
            issue_body=None,
            issue_number=None,
        )

    return ParsedEvent(
        delivery_id=delivery_id,
        event_type=event_header,
        action=action,
        repo=_repo_name(body),
        should_process=True,
        ignore_reason=None,
        issue_title=issue.get("title"),
        issue_body=issue.get("body"),
        issue_number=issue.get("number"),
    )


def _repo_name(body: dict[str, Any]) -> str | None:
    repo = body.get("repository")
    if isinstance(repo, dict):
        return repo.get("full_name")
    return None


def to_run_request(event: ParsedEvent) -> RunRequest:
    """Only call this when event.should_process is True."""
    return RunRequest(
        idempotency_key=event.delivery_id,
        event_type=f"{event.event_type}.{event.action}",
        target=event.repo,
        payload={
            "title": event.issue_title or "",
            "body": event.issue_body or "",
            "number": event.issue_number,
        },
    )


def record_ignored_event(ledger: Ledger, event: ParsedEvent) -> tuple[RunRecord, bool]:
    """Writes an IGNORED terminal record for an event that passed signature
    verification but should not be processed. Only call this when
    event.should_process is False.

    Mirrors runner.run_request's duplicate handling, on purpose: a redelivery
    of an already-ignored event must return the existing record rather than
    write a second row, the same idempotency-key guarantee every other path
    through this system relies on. There's no agent involved here, so this
    goes straight to begin_run/finish_run instead of through run_request.

    Returns (record, created), created is False when this delivery id was
    already seen.
    """
    record, created = ledger.begin_run(
        idempotency_key=event.delivery_id,
        event_type=f"{event.event_type}.{event.action or 'unknown'}",
        target=event.repo,
    )
    if not created:
        return record, False

    finished = ledger.finish_run(
        record.run_id,
        status=RunStatus.IGNORED,
        latency_ms=0.0,
        response={"ignore_reason": event.ignore_reason},
        outbound_action=OutboundAction.NONE,
    )
    return finished, True
