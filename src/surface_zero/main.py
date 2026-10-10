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
    allowed_repos: frozenset[str] | None = None,
    shadow_repos: frozenset[str] = frozenset(),
    agents_by_repo: dict[str, Agent] | None = None,
) -> FastAPI:
    """Builds the app from already-constructed dependencies, real or fake.
    Tests pass fakes here directly, nothing about this function knows or
    cares whether `agent` calls a real model or `poster` calls a real API.

    allowed_repos: None keeps the original behavior (no repo gating). A set
    means only those repos (case-insensitive) are ever processed; a signed
    event from any other repo is rejected before any ledger write, never run.
    Required in production once the GitHub App is public, because then anyone can install it and send signed events.

    shadow_repos: repos where the agent runs but no comment is attempted.

    agents_by_repo: when given, each repo is advised by its own agent (its own
    label vocabulary); keys are lowercase owner/name. A repo with no entry is
    rejected with no ledger row rather than falling back to some other repo's
    labels, which would recommend labels that do not exist there. When None,
    the single `agent` handles every repo (original behavior).
    """
    app = FastAPI(title="surface-zero")
    outbound = build_outbound_handler(poster, kill_switch, shadow_repos)
    allowed = None if allowed_repos is None else frozenset(r.lower() for r in allowed_repos)

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

        if allowed is not None and (event.repo is None or event.repo.lower() not in allowed):
            # Deliberately NO ledger row. The ledger describes this system's own
            # operation; once the App is public, a stranger's installation can
            # send validly signed events and would otherwise inflate the
            # ignored count in the public metrics. 200 so GitHub doesn't mark
            # the delivery as failing. Events with no repository at all
            # (installation, ping) are dropped the same way: with an allowlist
            # set, nothing unattributable to an allowed repo reaches the ledger.
            return Response(status_code=200, content="repo not on allowlist")

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

        chosen_agent = agent
        if agents_by_repo is not None:
            chosen_agent = agents_by_repo.get((event.repo or "").lower())
            if chosen_agent is None:
                return Response(status_code=200, content="no agent configured for repo")

        run_request(request, chosen_agent, ledger, outbound=outbound)
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


def parse_repo_list(raw: str | None) -> frozenset[str] | None:
    """Comma-separated owner/name list from an env var. None when unset or
    blank, which callers treat as "no gating", so an empty allowlist can never
    silently mean "allow everything" by being set to an empty string.
    """
    if raw is None or not raw.strip():
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


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

    # SURFACE_ZERO_LABELS may be a comma-separated list of label-set files, one
    # per repo (each file's own "repo" field says which repo it belongs to).
    label_sets = [LabelSet.from_file(p.strip()) for p in labels_path.split(",") if p.strip()]
    label_set = label_sets[0]
    agents_by_repo = {ls.repo.lower(): Advisor.from_env(ls) for ls in label_sets}

    # Multi-target: when SURFACE_ZERO_REPO_INSTALLATIONS is set, each repo gets
    # its own installation token via routing.py. Unset keeps the original
    # single-installation behavior untouched.
    from surface_zero.routing import (
        build_routed_clients,
        load_app_credentials_from_env,
        parse_repo_installations,
    )

    repo_installations = parse_repo_installations(os.environ.get("SURFACE_ZERO_REPO_INSTALLATIONS"))
    if repo_installations is not None:
        app_id, private_key = load_app_credentials_from_env()
        poster, candidate_fetcher = build_routed_clients(
            app_id=app_id,
            private_key_pem=private_key,
            repo_installations=repo_installations,
        )
    else:
        poster = GitHubCommentPoster.from_env()
        candidate_fetcher = GitHubCandidateFetcher.from_env()

    allowed = parse_repo_list(os.environ.get("SURFACE_ZERO_ALLOWED_REPOS"))
    if len(label_sets) > 1:
        if allowed is None:
            raise RuntimeError("multiple label sets require SURFACE_ZERO_ALLOWED_REPOS")
        missing = {r for r in allowed if r.lower() not in agents_by_repo}
        if missing:
            raise RuntimeError(f"allowlisted repos with no label set: {sorted(missing)}")

    return create_app(
        webhook_secret=webhook_secret,
        ledger=Ledger(ledger_path),
        kill_switch=KillSwitch(kill_switch_path),
        agent=agents_by_repo[label_set.repo.lower()],
        agents_by_repo=agents_by_repo if len(label_sets) > 1 else None,
        poster=poster,
        metrics_token=metrics_token,
        candidate_fetcher=candidate_fetcher,
        allowed_repos=allowed,
        shadow_repos=parse_repo_list(os.environ.get("SURFACE_ZERO_SHADOW_REPOS")) or frozenset(),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_production_app(), host="0.0.0.0", port=8000)
