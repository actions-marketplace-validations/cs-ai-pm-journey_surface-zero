"""Synthetic agents used to prove the ledger path before any real agent exists."""

from __future__ import annotations

from typing import Any

from surface_zero.runner import AgentResult, RunContext


def synthetic_success_agent(payload: dict[str, Any], ctx: RunContext) -> AgentResult:
    """Pretends to advise. Records one LLM call with unmeasured tokens and cost (NULL)."""
    ctx.record_llm_call(model="synthetic", latency_ms=0.0)
    return AgentResult(response={"suggested_label": "synthetic", "echo": payload})


def synthetic_abstain_agent(payload: dict[str, Any], ctx: RunContext) -> AgentResult:
    ctx.record_llm_call(model="synthetic", latency_ms=0.0)
    return AgentResult(response={"reason": "synthetic abstention"}, abstained=True)


def synthetic_failure_agent(payload: dict[str, Any], ctx: RunContext) -> AgentResult:
    """Makes an LLM call that fails, then raises. Both must land in the ledger."""
    ctx.record_llm_call(
        model="synthetic",
        latency_ms=0.0,
        error_type="SyntheticModelError",
        error_message="simulated model failure",
    )
    raise RuntimeError("synthetic agent failure")
