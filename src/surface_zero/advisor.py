"""The Tuesday advisor: issue in, one label suggestion out, or an explicit abstention.

Design constraints from the Block 11 docs, all enforced here:

* Structured output, not "please output JSON" prompting. The model can only
  respond by calling one of two tools. There is no free-text branch to parse.
* Enforced local label vocabulary. The tool schema's enum is built from the
  repo's actual LabelSet, and the label is re-checked in code after the call,
  a schema hint is not a guarantee, defense in depth is a Block 10 lesson.
* An explicit abstention path. `abstain` is a first-class tool, not a fallback
  the model reaches by inventing a bad label. This is the Block 10 finding,
  in code: a classifier with no path for "I don't know" hides its own error rate.
* Every LLM call reaches the ledger, including ones that fail to produce a
  usable tool call. A call that can't be parsed is still evidence.
* The prompt is written to behave like a junior contributor who knows local
  convention, not a universal triage expert, per the Week 2 build brief.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from surface_zero.labels import LabelSet
from surface_zero.runner import AgentResult, RunContext

# Claude Sonnet 5 pricing (per Anthropic's published rates), same source as
# agenteval/src/agenteval/judge.py. Tracked here so a pricing change is a
# one-line fix, not a hunt through every call site.
INPUT_COST_PER_MTOK = 2.00
OUTPUT_COST_PER_MTOK = 10.00

DEFAULT_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You triage incoming issues on one specific open-source repository, the \
way an attentive contributor who already knows this repo's conventions would, not the way a \
universal issue-classification service would.

You have exactly two actions available, and you must take exactly one:

- suggest_label: pick the single existing label that best fits this issue, from the repo's \
own label list below. Never propose a label that isn't in that list, and never guess when the \
fit is unclear, that's what abstain is for.
- abstain: decline to suggest a label. Use this whenever no label clearly applies, the issue \
is ambiguous, it needs a human to read more context than you have, or your confidence is low. \
Abstaining is a normal, expected outcome, not a failure.

Independently of which of those two actions you take, you may also flag this issue as a \
likely duplicate. Only do this if the message below lists other issues on this repository, \
and one of them clearly describes the same underlying problem or request as this one, not \
just the same general area. If so, set duplicate_of to that issue's number and \
duplicate_rationale to one sentence explaining the overlap. When you are not confident, or \
no candidate issues are listed, leave duplicate_of unset.

Some candidate issues are marked (closed) rather than (open). A closed issue is still a \
valid duplicate_of target -- in fact this case matters more, not less: if a new report \
substantially restates something already closed, that is a possible regression of \
already-resolved work, and is worth flagging exactly the same way as a match against an \
open issue. Do not let an issue's closed status make you less willing to flag it.

Repository: {repo}

Labels available on this repository:
{label_lines}
"""


class AdvisorError(Exception):
    """Raised when the model's response can't be turned into a safe action."""


def _validate_duplicate_reference(duplicate_of: int | None, payload: dict[str, Any]) -> None:
    """Defense in depth, same reasoning as the label re-check below: a schema hint
    is not a guarantee, so a duplicate_of the model invented must never reach a
    comment claiming an issue is a duplicate of something that was never offered
    to it as a candidate."""
    if duplicate_of is None:
        return
    candidate_numbers = {c.get("number") for c in payload.get("candidates") or []}
    if duplicate_of not in candidate_numbers:
        raise AdvisorError(
            f"model flagged issue #{duplicate_of} as a duplicate, but that number is not "
            "one of the candidate issues it was given"
        )


def _duplicate_state(duplicate_of: int | None, payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Looks up the matched candidate's actual state/state_reason from the
    payload rather than trusting anything the model said about it -- the model
    is never asked to report state back, so there is nothing for it to get
    wrong, but this keeps state determination on the deterministic side of
    the line the same way label validation and _validate_duplicate_reference
    already do."""
    if duplicate_of is None:
        return None, None
    for c in payload.get("candidates") or []:
        if c.get("number") == duplicate_of:
            return c.get("state", "open"), c.get("state_reason")
    return None, None


class SuggestLabelInput(BaseModel):
    label: str
    rationale: str
    duplicate_of: int | None = None
    duplicate_rationale: str | None = None


class AbstainInput(BaseModel):
    reason: str
    duplicate_of: int | None = None
    duplicate_rationale: str | None = None


def _tools(label_set: LabelSet) -> list[dict[str, Any]]:
    return [
        {
            "name": "suggest_label",
            "description": (
                "Suggest the single best-matching label for this issue, chosen only "
                "from this repository's existing label set."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "enum": list(label_set.names)},
                    "rationale": {
                        "type": "string",
                        "description": "One sentence explaining the match, in the repo's own terms.",
                    },
                    "duplicate_of": {
                        "type": ["integer", "null"],
                        "description": (
                            "The issue number of an existing open issue this substantially "
                            "restates, chosen only from the candidate issues listed in the "
                            "message. Omit or leave null otherwise."
                        ),
                    },
                    "duplicate_rationale": {
                        "type": ["string", "null"],
                        "description": "One sentence explaining the overlap. Required if duplicate_of is set.",
                    },
                },
                "required": ["label", "rationale"],
            },
        },
        {
            "name": "abstain",
            "description": (
                "Decline to suggest a label because no label clearly applies, the issue is "
                "ambiguous, or you are not confident enough to recommend one."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "duplicate_of": {
                        "type": ["integer", "null"],
                        "description": (
                            "The issue number of an existing open issue this substantially "
                            "restates, chosen only from the candidate issues listed in the "
                            "message. Omit or leave null otherwise."
                        ),
                    },
                    "duplicate_rationale": {
                        "type": ["string", "null"],
                        "description": "One sentence explaining the overlap. Required if duplicate_of is set.",
                    },
                },
                "required": ["reason"],
            },
        },
    ]


def _build_prompt(label_set: LabelSet) -> str:
    label_lines = "\n".join(f"- {l.name}: {l.description}" for l in label_set.labels)
    return SYSTEM_PROMPT.format(repo=label_set.repo, label_lines=label_lines)


def _truncate(text: str, limit: int = 300) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _issue_text(payload: dict[str, Any]) -> str:
    title = payload.get("title", "")
    body = payload.get("body", "")
    text = f"Title: {title}\n\nBody:\n{body}"

    candidates = payload.get("candidates") or []
    if candidates:
        candidate_lines = "\n\n".join(
            f"#{c['number']} ({c.get('state', 'open')}): {c.get('title', '')}\n"
            f"{_truncate(c.get('body', ''))}"
            for c in candidates
        )
        text += (
            "\n\n---\n"
            "Other issues on this repository (open and closed), listed for duplicate "
            "comparison only, they do not affect which label you choose:\n\n" + candidate_lines
        )
    return text


class AnthropicLike(Protocol):
    """The one method this module needs from an anthropic.Anthropic client.

    Kept narrow on purpose so tests can supply a stub without touching the
    real SDK or a network call.
    """

    class _Messages(Protocol):
        def create(self, **kwargs: Any) -> Any: ...

    messages: _Messages


def _cost_usd(usage: Any) -> float:
    return (
        usage.input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
        + usage.output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
    )


class Advisor:
    """Wraps an Anthropic client and a LabelSet into a runner-compatible agent."""

    def __init__(self, client: AnthropicLike, label_set: LabelSet, model: str = DEFAULT_MODEL):
        self.client = client
        self.label_set = label_set
        self.model = model

    @classmethod
    def from_env(cls, label_set: LabelSet, model: str = DEFAULT_MODEL) -> Advisor:
        """Builds a real client from ANTHROPIC_API_KEY. Not used in tests."""
        import os

        import anthropic
        from dotenv import load_dotenv

        load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to .env or export it before running."
            )
        return cls(anthropic.Anthropic(api_key=api_key), label_set, model=model)

    def __call__(self, payload: dict[str, Any], ctx: RunContext) -> AgentResult:
        """Runner-compatible agent entry point. See surface_zero.runner.Agent."""
        import time

        started = time.perf_counter()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=400,
                system=_build_prompt(self.label_set),
                tools=_tools(self.label_set),
                tool_choice={"type": "any"},
                messages=[{"role": "user", "content": _issue_text(payload)}],
            )
        except Exception as exc:  # noqa: BLE001 - the failed call itself must reach the ledger
            ctx.record_llm_call(
                model=self.model,
                latency_ms=(time.perf_counter() - started) * 1000,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

        latency_ms = (time.perf_counter() - started) * 1000
        cost_usd = _cost_usd(response.usage)

        tool_use = next(
            (block for block in response.content if getattr(block, "type", None) == "tool_use"),
            None,
        )
        if tool_use is None:
            ctx.record_llm_call(
                model=self.model,
                latency_ms=latency_ms,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cost_usd=cost_usd,
                error_type="AdvisorError",
                error_message="model responded without calling suggest_label or abstain",
            )
            raise AdvisorError("model did not call a tool, forced tool_choice was not honored")

        ctx.record_llm_call(
            model=self.model,
            latency_ms=latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=cost_usd,
        )

        if tool_use.name == "abstain":
            parsed = AbstainInput.model_validate(tool_use.input)
            _validate_duplicate_reference(parsed.duplicate_of, payload)
            response: dict[str, Any] = {"reason": parsed.reason}
            if parsed.duplicate_of is not None:
                response["duplicate_of"] = parsed.duplicate_of
                response["duplicate_rationale"] = parsed.duplicate_rationale
                state, state_reason = _duplicate_state(parsed.duplicate_of, payload)
                response["duplicate_of_state"] = state
                response["duplicate_of_state_reason"] = state_reason
            return AgentResult(response=response, abstained=True)

        if tool_use.name == "suggest_label":
            parsed = SuggestLabelInput.model_validate(tool_use.input)
            if parsed.label not in self.label_set:
                # Defense in depth: the schema enum should prevent this, but a schema
                # hint is not a guarantee, and a bad label must never reach a comment.
                raise AdvisorError(
                    f"model suggested label {parsed.label!r} which is not in "
                    f"{self.label_set.repo}'s label set"
                )
            _validate_duplicate_reference(parsed.duplicate_of, payload)
            response: dict[str, Any] = {"suggested_label": parsed.label, "rationale": parsed.rationale}
            if parsed.duplicate_of is not None:
                response["duplicate_of"] = parsed.duplicate_of
                response["duplicate_rationale"] = parsed.duplicate_rationale
                state, state_reason = _duplicate_state(parsed.duplicate_of, payload)
                response["duplicate_of_state"] = state
                response["duplicate_of_state_reason"] = state_reason
            return AgentResult(response=response)

        raise AdvisorError(f"model called unknown tool {tool_use.name!r}")
