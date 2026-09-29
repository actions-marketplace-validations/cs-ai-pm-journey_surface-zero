"""Run orchestration. The ledger row exists before the agent is ever called.

Order of operations for every request:

1. begin_run: durable `started` row (or detect a duplicate delivery and stop).
2. call the agent, handing it a context that writes LLM calls straight to the ledger.
3. if the agent produced a usable suggestion (not abstained, not failed) and an
   outbound handler was supplied, run it now, before the terminal write. This is
   where a kill switch decides whether a comment actually gets posted.
4. finish_run: exactly one terminal state, and it captures both the agent's
   outcome and the outbound outcome together. Terminal states are immutable, so
   this has to be one write, not "finish, then separately record what happened
   outbound" -- there is no safe second write to a row that can no longer change.

An exception inside the agent, or inside the outbound handler, never escapes to
the caller. Both become a `failed` ledger record. The caller (a webhook handler)
gets a result it can safely acknowledge, so a failing agent or a failing GitHub
API call cannot turn into a retry storm, duplicate delivery is still caught by
begin_run's idempotency key regardless of how the previous attempt ended.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from surface_zero.ledger import (
    Ledger,
    LlmCallRecord,
    OutboundAction,
    RunRecord,
    RunStatus,
)


class RunRequest(BaseModel):
    idempotency_key: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    target: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    response: dict[str, Any] = Field(default_factory=dict)
    abstained: bool = False


class RunContext:
    """What an agent is allowed to touch: the ledger, scoped to its own run."""

    def __init__(self, ledger: Ledger, run_id: str):
        self._ledger = ledger
        self.run_id = run_id

    def record_llm_call(
        self,
        *,
        model: str | None = None,
        latency_ms: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> LlmCallRecord:
        return self._ledger.record_llm_call(
            self.run_id,
            model=model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            error_type=error_type,
            error_message=error_message,
        )


Agent = Callable[[dict[str, Any], RunContext], AgentResult]


class OutboundResult(BaseModel):
    """What an outbound handler decides happened. response_patch is merged
    into the ledger response so, e.g., a posted comment's URL ends up on the
    same record as the suggestion that produced it."""

    action: OutboundAction
    response_patch: dict[str, Any] = Field(default_factory=dict)


OutboundHandler = Callable[["RunRequest", AgentResult], OutboundResult]


class RunOutcome(BaseModel):
    run: RunRecord
    duplicate: bool


def run_request(
    request: RunRequest,
    agent: Agent,
    ledger: Ledger,
    outbound: OutboundHandler | None = None,
) -> RunOutcome:
    record, created = ledger.begin_run(
        idempotency_key=request.idempotency_key,
        event_type=request.event_type,
        target=request.target,
        request=request.payload,
    )
    if not created:
        # Duplicate delivery. Never re-run the agent, never touch the existing record.
        return RunOutcome(run=record, duplicate=True)

    ctx = RunContext(ledger, record.run_id)
    started = time.perf_counter()
    try:
        result = agent(request.payload, ctx)
    except Exception as exc:  # noqa: BLE001 - every failure must reach the ledger
        finished = ledger.finish_run(
            record.run_id,
            status=RunStatus.FAILED,
            latency_ms=(time.perf_counter() - started) * 1000,
            error_type=type(exc).__name__,
            error_message=str(exc),
            outbound_action=OutboundAction.NONE,
        )
        return RunOutcome(run=finished, duplicate=False)

    outbound_action = OutboundAction.NONE
    response = result.response

    if outbound is not None and not result.abstained:
        try:
            outcome = outbound(request, result)
        except Exception as exc:  # noqa: BLE001 - a failed post must reach the ledger too
            finished = ledger.finish_run(
                record.run_id,
                status=RunStatus.FAILED,
                latency_ms=(time.perf_counter() - started) * 1000,
                response=result.response,
                outbound_action=OutboundAction.NONE,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return RunOutcome(run=finished, duplicate=False)
        outbound_action = outcome.action
        if outcome.response_patch:
            response = {**result.response, **outcome.response_patch}

    finished = ledger.finish_run(
        record.run_id,
        status=RunStatus.ABSTAINED if result.abstained else RunStatus.SUCCEEDED,
        latency_ms=(time.perf_counter() - started) * 1000,
        response=response,
        outbound_action=outbound_action,
    )
    return RunOutcome(run=finished, duplicate=False)
