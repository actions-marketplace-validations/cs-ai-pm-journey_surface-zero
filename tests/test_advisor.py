import pytest

from surface_zero.advisor import Advisor, AdvisorError
from surface_zero.labels import LabelSet
from surface_zero.ledger import Ledger, RunStatus
from surface_zero.runner import RunRequest, run_request
from tests.fake_anthropic import FakeAnthropic, FakeResponse, TextBlock, ToolUseBlock


@pytest.fixture
def label_set():
    return LabelSet.from_file("tests/fixtures/labels.json")


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.db")


def issue_request(key="issue-1", candidates=None):
    payload = {"title": "App crashes on launch", "body": "Reproduces every time."}
    if candidates is not None:
        payload["candidates"] = candidates
    return RunRequest(
        idempotency_key=key,
        event_type="issue.opened",
        target="fixture/example-repo",
        payload=payload,
    )


def test_suggest_label_is_recorded_end_to_end(label_set, ledger):
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    ToolUseBlock(
                        name="suggest_label",
                        input={"label": "bug", "rationale": "Reports a crash on startup."},
                    )
                ]
            )
        ]
    )
    advisor = Advisor(fake, label_set)
    outcome = run_request(issue_request(), advisor, ledger)

    assert outcome.run.status == RunStatus.SUCCEEDED
    assert outcome.run.response == {"suggested_label": "bug", "rationale": "Reports a crash on startup."}

    (call,) = ledger.llm_calls_for_run(outcome.run.run_id)
    assert call.input_tokens == 50 and call.output_tokens == 20
    assert call.cost_usd == pytest.approx(50 / 1_000_000 * 2.00 + 20 / 1_000_000 * 10.00)
    assert call.error_type is None

    # tool_choice forces exactly one of the two actions, never free text
    assert fake.messages.calls[0]["tool_choice"] == {"type": "any"}
    tool_names = {t["name"] for t in fake.messages.calls[0]["tools"]}
    assert tool_names == {"suggest_label", "abstain"}
    # the schema enum is built from the repo's real labels, nothing invented
    suggest_schema = next(t for t in fake.messages.calls[0]["tools"] if t["name"] == "suggest_label")
    assert set(suggest_schema["input_schema"]["properties"]["label"]["enum"]) == set(label_set.names)


def test_abstain_is_recorded_as_its_own_terminal_state(label_set, ledger):
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[ToolUseBlock(name="abstain", input={"reason": "Not enough detail to tell."})]
            )
        ]
    )
    outcome = run_request(issue_request(), Advisor(fake, label_set), ledger)
    assert outcome.run.status == RunStatus.ABSTAINED
    assert outcome.run.response == {"reason": "Not enough detail to tell."}


def test_label_outside_the_local_vocabulary_is_rejected_not_trusted(label_set, ledger):
    """Defense in depth: even if the schema enum were somehow bypassed, a label
    that isn't in this repo's real set must never become a suggestion."""
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    ToolUseBlock(
                        name="suggest_label",
                        input={"label": "not-a-real-label", "rationale": "made up"},
                    )
                ]
            )
        ]
    )
    outcome = run_request(issue_request(), Advisor(fake, label_set), ledger)
    assert outcome.run.status == RunStatus.FAILED
    assert "not-a-real-label" in outcome.run.error_message
    # the bad call is still on record even though it was rejected
    (call,) = ledger.llm_calls_for_run(outcome.run.run_id)
    assert call.error_type is None  # the API call itself succeeded, the content was rejected after


def test_model_responding_without_a_tool_call_is_a_failure_not_a_silent_skip(label_set, ledger):
    fake = FakeAnthropic(responses=[FakeResponse(content=[TextBlock(text="I think it's a bug")])])
    outcome = run_request(issue_request(), Advisor(fake, label_set), ledger)
    assert outcome.run.status == RunStatus.FAILED
    assert "did not call a tool" in outcome.run.error_message
    (call,) = ledger.llm_calls_for_run(outcome.run.run_id)
    assert call.error_type == "AdvisorError"


def test_api_error_is_recorded_before_it_propagates(label_set, ledger):
    fake = FakeAnthropic(error=RuntimeError("connection reset"))
    outcome = run_request(issue_request(), Advisor(fake, label_set), ledger)
    assert outcome.run.status == RunStatus.FAILED
    assert outcome.run.error_type == "RuntimeError"
    (call,) = ledger.llm_calls_for_run(outcome.run.run_id)
    assert call.error_type == "RuntimeError"
    assert call.input_tokens is None  # no usage data, the call never returned


def test_unknown_tool_name_is_rejected(label_set, ledger):
    fake = FakeAnthropic(responses=[FakeResponse(content=[ToolUseBlock(name="close_issue", input={})])])
    outcome = run_request(issue_request(), Advisor(fake, label_set), ledger)
    assert outcome.run.status == RunStatus.FAILED
    assert "unknown tool" in outcome.run.error_message


def test_prompt_names_the_repo_and_every_label(label_set, ledger):
    fake = FakeAnthropic(
        responses=[FakeResponse(content=[ToolUseBlock(name="abstain", input={"reason": "n/a"})])]
    )
    run_request(issue_request(), Advisor(fake, label_set), ledger)
    system_prompt = fake.messages.calls[0]["system"]
    assert label_set.repo in system_prompt
    for label in label_set.labels:
        assert label.name in system_prompt


def test_duplicate_delivery_never_calls_the_model_twice(label_set, ledger):
    fake = FakeAnthropic(
        responses=[
            FakeResponse(content=[ToolUseBlock(name="abstain", input={"reason": "n/a"})]),
            FakeResponse(content=[ToolUseBlock(name="abstain", input={"reason": "n/a"})]),
        ]
    )
    advisor = Advisor(fake, label_set)
    run_request(issue_request("dup"), advisor, ledger)
    run_request(issue_request("dup"), advisor, ledger)
    assert len(fake.messages.calls) == 1


def test_duplicate_flag_is_carried_alongside_a_label_suggestion(label_set, ledger):
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    ToolUseBlock(
                        name="suggest_label",
                        input={
                            "label": "bug",
                            "rationale": "Reports a crash on startup.",
                            "duplicate_of": 3,
                            "duplicate_rationale": "Same crash-on-launch report as #3.",
                        },
                    )
                ]
            )
        ]
    )
    candidates = [{"number": 3, "title": "App crashes at startup", "body": "Happens every time."}]
    outcome = run_request(issue_request(candidates=candidates), Advisor(fake, label_set), ledger)

    assert outcome.run.status == RunStatus.SUCCEEDED
    assert outcome.run.response["duplicate_of"] == 3
    assert outcome.run.response["duplicate_rationale"] == "Same crash-on-launch report as #3."

    # the candidate issue text actually reached the model
    user_message = fake.messages.calls[0]["messages"][0]["content"]
    assert "#3" in user_message and "App crashes at startup" in user_message


def test_duplicate_flag_is_carried_alongside_abstain(label_set, ledger):
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    ToolUseBlock(
                        name="abstain",
                        input={
                            "reason": "Not enough detail to pick a label.",
                            "duplicate_of": 7,
                            "duplicate_rationale": "Restates #7's missing-data report.",
                        },
                    )
                ]
            )
        ]
    )
    candidates = [{"number": 7, "title": "Data vanished", "body": "Saved items gone."}]
    outcome = run_request(issue_request(candidates=candidates), Advisor(fake, label_set), ledger)

    assert outcome.run.status == RunStatus.ABSTAINED
    assert outcome.run.response["duplicate_of"] == 7


def test_no_duplicate_flag_means_no_duplicate_fields_in_the_response(label_set, ledger):
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[ToolUseBlock(name="suggest_label", input={"label": "bug", "rationale": "crash"})]
            )
        ]
    )
    outcome = run_request(issue_request(), Advisor(fake, label_set), ledger)
    assert "duplicate_of" not in outcome.run.response
    assert "duplicate_rationale" not in outcome.run.response


def test_duplicate_state_is_looked_up_not_trusted_from_the_model(label_set, ledger):
    """The model is never asked to report a candidate's open/closed state --
    advisor.py looks it up itself from the payload, same defense-in-depth
    reasoning as _validate_duplicate_reference. This is what lets pipeline.py
    render a closed-duplicate match differently without trusting anything
    the model said about status."""
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    ToolUseBlock(
                        name="suggest_label",
                        input={
                            "label": "bug",
                            "rationale": "Same crash as the closed report.",
                            "duplicate_of": 3,
                            "duplicate_rationale": "Restates the closed crash report.",
                        },
                    )
                ]
            )
        ]
    )
    candidates = [
        {
            "number": 3,
            "title": "App crashes at startup",
            "body": "Happens every time.",
            "state": "closed",
            "state_reason": "completed",
        }
    ]
    outcome = run_request(issue_request(candidates=candidates), Advisor(fake, label_set), ledger)

    assert outcome.run.response["duplicate_of_state"] == "closed"
    assert outcome.run.response["duplicate_of_state_reason"] == "completed"

    # the candidate's closed status actually reached the model's context too
    user_message = fake.messages.calls[0]["messages"][0]["content"]
    assert "#3 (closed)" in user_message


def test_duplicate_state_defaults_to_open_when_unspecified(label_set, ledger):
    """Candidates from before this field existed (or a fetcher that never
    sets it) must not break -- absence means open, not an error."""
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    ToolUseBlock(
                        name="suggest_label",
                        input={
                            "label": "bug",
                            "rationale": "crash",
                            "duplicate_of": 3,
                            "duplicate_rationale": "same",
                        },
                    )
                ]
            )
        ]
    )
    candidates = [{"number": 3, "title": "App crashes at startup", "body": "..."}]
    outcome = run_request(issue_request(candidates=candidates), Advisor(fake, label_set), ledger)
    assert outcome.run.response["duplicate_of_state"] == "open"
    assert outcome.run.response["duplicate_of_state_reason"] is None


def test_duplicate_of_an_unlisted_issue_is_rejected_not_trusted(label_set, ledger):
    """Same defense-in-depth reasoning as the invented-label test: a duplicate_of
    the model invented, that was never one of the candidates it was given, must
    never reach a comment claiming a real overlap."""
    fake = FakeAnthropic(
        responses=[
            FakeResponse(
                content=[
                    ToolUseBlock(
                        name="suggest_label",
                        input={
                            "label": "bug",
                            "rationale": "crash",
                            "duplicate_of": 999,
                            "duplicate_rationale": "made up",
                        },
                    )
                ]
            )
        ]
    )
    candidates = [{"number": 3, "title": "Something else", "body": "..."}]
    outcome = run_request(issue_request(candidates=candidates), Advisor(fake, label_set), ledger)
    assert outcome.run.status == RunStatus.FAILED
    assert "#999" in outcome.run.error_message


def test_no_candidates_means_no_duplicate_section_in_the_prompt(label_set, ledger):
    fake = FakeAnthropic(
        responses=[FakeResponse(content=[ToolUseBlock(name="abstain", input={"reason": "n/a"})])]
    )
    run_request(issue_request(), Advisor(fake, label_set), ledger)
    user_message = fake.messages.calls[0]["messages"][0]["content"]
    assert "Other issues currently open" not in user_message
