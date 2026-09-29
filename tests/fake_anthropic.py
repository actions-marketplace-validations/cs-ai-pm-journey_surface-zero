"""A minimal stand-in for the one shape advisor.py reads off an Anthropic response.

Not a mock of the whole SDK, just the attributes _issue_text/_tools/Advisor
actually touch: response.content (blocks with .type/.name/.input) and
response.usage (input_tokens/output_tokens). Keeping this narrow means the
tests exercise Advisor's own logic, not anthropic's client internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class Usage:
    input_tokens: int = 50
    output_tokens: int = 20


@dataclass
class FakeResponse:
    content: list[Any]
    usage: Usage = field(default_factory=Usage)


class FakeMessages:
    def __init__(self, responses: list[FakeResponse] | None = None, error: Exception | None = None):
        self._responses = responses or []
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._responses[len(self.calls) - 1]


class FakeAnthropic:
    def __init__(self, responses: list[FakeResponse] | None = None, error: Exception | None = None):
        self.messages = FakeMessages(responses=responses, error=error)
