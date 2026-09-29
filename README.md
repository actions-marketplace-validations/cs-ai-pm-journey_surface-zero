# surface-zero

Advisory-only GitHub issue triage agent (Block 11, Gate A). It reads new issues on a
maintainer-consented repo and posts one comment suggesting a label from the repo's own
label set, plus a duplicate flag. Nothing else.

## Status

Week 2, Monday: ledger-first skeleton. A request creates a durable ledger record before
the agent does anything, and every run ends in exactly one terminal state.

## Setup

```bash
uv sync
uv run pytest
```

## Try the ledger path

```bash
uv run surface-zero synthetic --mode success
uv run surface-zero synthetic --mode failure
uv run surface-zero synthetic --mode abstain

# run the same command twice with the same --key to see duplicate detection
uv run surface-zero synthetic --mode success --key same-key
uv run surface-zero synthetic --mode success --key same-key

uv run surface-zero runs
```

The ledger is a SQLite file at `data/ledger.db` (gitignored).

## Webhook path (target-agnostic parts, Wednesday build)

`src/surface_zero/webhook.py` verifies a GitHub webhook's HMAC-SHA256 signature and
classifies the event: only `issues` events with `action=opened`, from a non-bot author,
become a `RunRequest`. Everything else that passes signature verification (pull requests,
other issue actions, bot-authored issues) is recorded via `record_ignored_event` as its
own terminal status, `RunStatus.IGNORED`, deliberately distinct from `ABSTAINED`:
ignored means the event was out of scope and the advisor was never called, abstained
means the advisor looked and declined. Ledger-first means every legitimate delivery is
evidence, whether or not it was actionable. A malformed payload (bad JSON, missing
headers) raises `MalformedEventError` instead, since there may not even be a usable
idempotency key to record against.

`src/surface_zero/killswitch.py` gates the outbound comment specifically, not the model
call. Two ways to trip it: a `SURFACE_ZERO_KILL` env var (instant, no repo write access
needed) or a `data/kill_switch.json` file (durable, survives a restart). A kill switch
file that fails to parse is treated as active, it fails safe.

`src/surface_zero/pipeline.py` wires a `CommentPoster` and a `KillSwitch` into the
`outbound` handler `runner.run_request` now accepts. The real `GitHubCommentPoster`
went live 2026-09-23 once ForgeAS/everForge was confirmed as the target, and switched
2026-09-24 to authenticating as the `surface-zero-advisor` GitHub App instead of a
personal PAT -- see `comment_poster.py`'s module docstring for why, and
`GitHubCommentPoster.from_env` for the `SURFACE_ZERO_GITHUB_APP_*` env vars it needs
(`SURFACE_ZERO_GITHUB_APP_ID`, `SURFACE_ZERO_GITHUB_INSTALLATION_ID`, and either
`SURFACE_ZERO_GITHUB_PRIVATE_KEY` or `SURFACE_ZERO_GITHUB_PRIVATE_KEY_PATH`). Either
way, it self-checks its own token's effective permission on the target repo before
posting anything, and refuses to post if that permission is anything beyond
Issues: Read and write.

`src/surface_zero/main.py` is the HTTP boundary. `create_app(...)` takes already-built
dependencies (real or fake) and wires one route, `POST /webhook`. Response codes are
deliberate: 401 for a bad signature (no ledger row, not treated as a GitHub event at
all), 400 for a signature-valid but unparseable payload, 200 for everything else,
including a run that finished FAILED, because GitHub's own redelivery reuses the same
delivery id, and idempotency means that redelivery would just be detected as a
duplicate and skipped, not actually retried. A failed run gets caught by a human during
the Friday ops review, not by GitHub's retry behavior.

Fully synchronous: verify, parse, run the advisor, attempt to post, respond. Agreed with
Adam on 2026-09-22 given this bot's expected volume; revisit if it ever needs to scale.

Run it for real with `SURFACE_ZERO_WEBHOOK_SECRET` set (and `ANTHROPIC_API_KEY` in
`.env`): `uv run python -m surface_zero.main`. It will accept and log webhooks today;
it cannot yet post a real comment, `GitHubCommentPoster` is still a deliberate
`NotImplementedError` pending a confirmed target.

## Ledger rules

- Row is written as `started` before the agent runs. A row stuck in `started` is an orphan.
- `idempotency_key` is UNIQUE. Duplicate deliveries return the existing run and never re-run the agent.
- Terminal states (`succeeded`, `abstained`, `failed`, `suppressed`) are immutable.
- Token, cost and latency fields are nullable. NULL means not measured, not zero.
