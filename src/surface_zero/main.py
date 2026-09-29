"""The HTTP boundary: receives GitHub's webhook POST and drives it through the
pipeline built Monday through Wednesday. Everything downstream of create_app
was already tested against fakes, this file's own tests use FastAPI's
TestClient against those same fakes, so nothing here needs a real API key,
a real repo, or a real GitHub webhook secret to test.

Response codes, and why:

* 401: signature didn't verify. Not treated as a GitHub event at all, no
  ledger row is written, see webhook.verify_signature.
* 400: signature verified but the payload couldn't be parsed (bad JSON,
  missing GitHub headers). There often isn't even a usable idempotency key
  to record against, see MalformedEventError.
* 200: everything else, including a run that finished FAILED. This is a
  deliberate consequence of idempotency, not papering over failure: GitHub's
  own "Redeliver" button reuses the same X-GitHub-Delivery id, and begin_run's
  UNIQUE constraint means that redelivery would just be detected as a
  duplicate and skipped, not actually re-attempted. Returning a 5xx to
  *invite* a retry would be dishonest about what that retry would accomplish.
  A failed run gets caught by a human during the Friday ops review, not by
  GitHub's retry behavior, the same human-reviewed-not-auto-retried call
  already made about ledger orphans on Monday.

Fully synchronous by design: verify, parse, run the advisor, attempt to post,
respond. Agreed with Adam on 2026-09-22 given this bot's volume, a handful of
issues a day on one repo. Revisit for a background-task model if Surface Zero
ever runs against something busier, at which point "the ledger row is stuck
in started because the background task merely got queued, not because
anything crashed" needs its own design pass, that's a different failure
shape than the orphan handling built for the synchronous path.
"""

from __future__ import annotations

import hmac

from fastapi import FastAPI, Request, Response

from surface_zero.candidate_fetcher import CandidateFetcher
from surface_zero.comment_poster import CommentPoster
from surface_zero.killswitch import KillSwitch
from surface_zero.ledger import Ledger
from surface_zero.pipeline import build_outbound_handler
from surface_zero.runner import Agent, run_request
from surface_zero.webhook import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    MalformedEventError,
    SignatureError,
    parse_event,
    record_ignored_event,
    to_run_request,
    verify_signature,
)


def create_app(
    *,
    webhook_secret: str,
    ledger: Ledger,
    kill_switch: KillSwitch,
    agent: Agent,
    poster: CommentPoster,
    metrics_token: str | None = None,
    candidate_fetcher: CandidateFetcher | None = None,
) -> FastAPI:
    """Builds the app from already-constructed dependencies, real or fake.
    Tests pass fakes here directly, nothing about this function knows or
    cares whether `agent` calls a real model or `poster` calls a real API.
    """
    app = FastAPI(title="surface-zero")
    outbound = build_outbound_handler(poster, kill_switch)

    @app.post("/webhook")
    async def webhook(request: Request) -> Response:
        body = await request.body()

        try:
            verify_signature(webhook_secret, body, request.headers.get(SIGNATURE_HEADER))
        except SignatureError as exc:
            return Response(status_code=401, content=str(exc))

        try:
            event = parse_event(
                event_header=request.headers.get(EVENT_HEADER),
                delivery_id=request.headers.get(DELIVERY_HEADER),
                payload_bytes=body,
            )
        except MalformedEventError as exc:
            return Response(status_code=400, content=str(exc))

        if not event.should_process:
            record_ignored_event(ledger, event)
            return Response(status_code=200, content="ignored")

        request = to_run_request(event)
        # Fetch other open issues to compare against for duplicate detection.
        # Added 2026-09-29: this was the missing half of the pipeline --
        # advisor.py's duplicate-flagging logic was always correct and
        # tested, it just never received any candidates in production (only
        # cli.py's --candidates flag populated this, for the offline blind-
        # test harness). A fetch failure here must never take down the run
        # itself -- same "never let a side concern crash the main path"
        # discipline as the rest of this file -- so it degrades to no
        # candidates (equivalent to today's behavior) rather than failing
        # the whole webhook.
        if candidate_fetcher is not None and event.issue_number is not None:
            try:
                request.payload["candidates"] = candidate_fetcher.fetch(
                    repo=event.repo, exclude_number=event.issue_number
                )
            except Exception:
                pass

        run_request(request, agent, ledger, outbound=outbound)
        return Response(status_code=200, content="processed")

    @app.get("/metrics/ledger.ndjson")
    async def metrics_ledger(request: Request) -> Response:
        """Feeds the portfolio's publish-metrics.yml Action. See metrics.py
        for exactly what is and isn't in each row, and why.
        """
        from surface_zero.metrics import ledger_rows_as_ndjson

        provided = request.headers.get("authorization", "")
        expected = f"Bearer {metrics_token}"
        if not metrics_token or not hmac.compare_digest(provided, expected):
            return Response(status_code=401, content="unauthorized")
        return Response(
            content=ledger_rows_as_ndjson(ledger),
            media_type="application/x-ndjson",
        )

    return app


def build_production_app() -> FastAPI:
    """Wires real dependencies from the environment. Deferred imports and
    deferred env reads on purpose, so importing this module (as the test
    suite does, indirectly, by importing create_app) never requires
    ANTHROPIC_API_KEY or SURFACE_ZERO_WEBHOOK_SECRET to be set.

    GitHubCommentPoster is real as of 2026-09-23, once ForgeAS/everForge was
    confirmed as the target: it self-checks its own token's permission on
    the target repo before it will post anything (see comment_poster.py).
    As of 2026-09-24 it authenticates as the surface-zero-advisor GitHub
    App rather than a personal PAT, so comments post under a bot identity
    instead of Adam's own account -- see GitHubCommentPoster.from_env's
    docstring for the SURFACE_ZERO_GITHUB_APP_* env vars this needs.

    SURFACE_ZERO_METRICS_TOKEN, added 2026-09-24, gates GET /metrics/ledger.ndjson,
    the portfolio site's nightly metrics pull. Unset means that route always
    401s rather than being silently open.
    """
    import os

    from surface_zero.advisor import Advisor
    from surface_zero.candidate_fetcher import GitHubCandidateFetcher
    from surface_zero.comment_poster import GitHubCommentPoster
    from surface_zero.labels import LabelSet

    webhook_secret = os.environ["SURFACE_ZERO_WEBHOOK_SECRET"]
    labels_path = os.environ.get("SURFACE_ZERO_LABELS", "data/label_sets/fixture-repo.json")
    ledger_path = os.environ.get("SURFACE_ZERO_DB", "data/ledger.db")
    kill_switch_path = os.environ.get("SURFACE_ZERO_KILL_SWITCH_FILE", "data/kill_switch.json")
    # Unset means /metrics/ledger.ndjson always 401s, not that it's open. See
    # README for how to generate one and where it goes (Fly secret + the
    # portfolio repo's LEDGER_TOKEN Actions secret, matching values).
    metrics_token = os.environ.get("SURFACE_ZERO_METRICS_TOKEN")

    label_set = LabelSet.from_file(labels_path)
    return create_app(
        webhook_secret=webhook_secret,
        ledger=Ledger(ledger_path),
        kill_switch=KillSwitch(kill_switch_path),
        agent=Advisor.from_env(label_set),
        poster=GitHubCommentPoster.from_env(),
        metrics_token=metrics_token,
        candidate_fetcher=GitHubCandidateFetcher.from_env(),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_production_app(), host="0.0.0.0", port=8000)
