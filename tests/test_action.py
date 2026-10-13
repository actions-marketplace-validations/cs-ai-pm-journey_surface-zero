import json

import httpx
import pytest

from surface_zero import action
from surface_zero.action import (
    MARKER,
    ActionGitHub,
    ConfigError,
    build_label_set,
    export_record,
    load_config,
    load_event,
    run,
    run_stats,
    write_outputs,
)
from surface_zero.advisor import Advisor
from surface_zero.comment_poster import GitHubCommentPoster
from surface_zero.ledger import Ledger
from tests.fake_anthropic import FakeAnthropic, FakeResponse, ToolUseBlock

REPO = "acme/widgets"

REPO_LABELS = [
    {"name": "bug", "description": "Something isn't working"},
    {"name": "enhancement", "description": "New feature or request"},
    {"name": "duplicate", "description": "This issue or pull request already exists"},
    {"name": "wontfix", "description": "This will not be worked on"},
]


def base_env(tmp_path, **overrides):
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "action": "opened",
                "repository": {"full_name": REPO},
                "issue": {"number": 7, "title": "App crashes on start", "body": "It crashes."},
            }
        )
    )
    env = {
        "INPUT_ANTHROPIC-API-KEY": "sk-test",
        "GITHUB_TOKEN": "gh-test",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_EVENT_NAME": "issues",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    env.update(overrides)
    return env


# ------------------------------------------------------------------ config


def test_missing_api_key_tells_the_maintainer_what_to_do(tmp_path):
    env = base_env(tmp_path)
    del env["INPUT_ANTHROPIC-API-KEY"]
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config(env)


def test_defaults_are_the_minimum_a_stranger_provides(tmp_path):
    cfg = load_config(base_env(tmp_path))
    assert cfg.labels == ()
    assert cfg.confidence_threshold == 0.0
    assert cfg.duplicates is False and cfg.summaries is False and cfg.dry_run is False


@pytest.mark.parametrize("raw", ["yes", "1", "TRUE ish"])
def test_booleans_are_strict(tmp_path, raw):
    with pytest.raises(ConfigError, match="'true' or 'false'"):
        load_config(base_env(tmp_path, **{"INPUT_DRY-RUN": raw}))


@pytest.mark.parametrize("raw", ["abc", "1.5", "-0.1"])
def test_threshold_must_be_a_number_between_0_and_1(tmp_path, raw):
    with pytest.raises(ConfigError, match="confidence-threshold"):
        load_config(base_env(tmp_path, **{"INPUT_CONFIDENCE-THRESHOLD": raw}))


@pytest.mark.parametrize(
    "name,value",
    [("INPUT_DUPLICATES", "true"), ("INPUT_SUMMARIES", "true")],
)
def test_features_not_yet_built_fail_loudly_instead_of_being_ignored(tmp_path, name, value):
    with pytest.raises(ConfigError, match="Not available in this version"):
        load_config(base_env(tmp_path, **{name: value}))


def test_labels_input_is_split_and_trimmed(tmp_path):
    cfg = load_config(base_env(tmp_path, INPUT_LABELS=" bug , enhancement ,"))
    assert cfg.labels == ("bug", "enhancement")


# ------------------------------------------------------------------- event


def test_issue_opened_event_is_parsed(tmp_path):
    ev = load_event(base_env(tmp_path))
    assert (ev.repo, ev.number, ev.title) == (REPO, 7, "App crashes on start")


def test_event_for_a_different_repo_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="does not match"):
        load_event(base_env(tmp_path, GITHUB_REPOSITORY="other/repo"))


def test_malformed_repository_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="GITHUB_REPOSITORY"):
        load_event(base_env(tmp_path, GITHUB_REPOSITORY="not a repo"))


def test_missing_event_file_is_a_setup_error(tmp_path):
    with pytest.raises(ConfigError, match="GITHUB_EVENT_PATH"):
        load_event(base_env(tmp_path, GITHUB_EVENT_PATH=str(tmp_path / "nope.json")))


# ------------------------------------------------------------------ labels


def test_default_label_set_excludes_closure_labels():
    ls = build_label_set(REPO, REPO_LABELS, ())
    assert ls.names == ("bug", "enhancement")


def test_explicit_labels_can_include_closure_labels():
    ls = build_label_set(REPO, REPO_LABELS, ("bug", "wontfix"))
    assert ls.names == ("bug", "wontfix")


def test_unknown_label_is_a_setup_error():
    with pytest.raises(ConfigError, match="do not exist"):
        build_label_set(REPO, REPO_LABELS, ("bug", "nonsense"))


def test_a_repo_with_too_few_labels_is_a_setup_error():
    with pytest.raises(ConfigError, match="at least 2"):
        build_label_set(REPO, [{"name": "bug", "description": ""}], ())


# --------------------------------------------------------------------- run


class Recorder:
    def __init__(self, comments=None, labels=None, post_status=201):
        self.posts = []
        self.comments = comments or []
        self.labels = REPO_LABELS if labels is None else labels
        self.post_status = post_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO})
        if request.method == "GET" and path == f"/repos/{REPO}/labels":
            return httpx.Response(200, json=self.labels)
        if request.method == "GET" and path == f"/repos/{REPO}/issues/7/comments":
            return httpx.Response(200, json=self.comments)
        if request.method == "POST" and path == f"/repos/{REPO}/issues/7/comments":
            self.posts.append(json.loads(request.content)["body"])
            if self.post_status != 201:
                return httpx.Response(self.post_status, json={"message": "Resource not accessible by integration"})
            return httpx.Response(201, json={"html_url": f"https://github.com/{REPO}/issues/7#c1"})
        raise AssertionError(f"unexpected request {request.method} {path}")

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler), base_url="https://api.github.com")


def suggest(label="bug", confidence=0.9):
    inp = {"label": label, "rationale": "It crashes."}
    if confidence is not None:
        inp["confidence"] = confidence
    return FakeResponse(content=[ToolUseBlock(name="suggest_label", input=inp)])


def go(tmp_path, rec, model, env=None, config=None):
    env = env or base_env(tmp_path)
    cfg = config or load_config(env)
    ledger = Ledger(":memory:")
    result = run(
        env,
        github=ActionGitHub("gh-test", client=rec.client()),
        advisor_factory=lambda ls: Advisor(model, ls, confidence_threshold=cfg.confidence_threshold),
        poster=GitHubCommentPoster("gh-test", client=rec.client(), scope_policy="workflow"),
        ledger=ledger,
        config=cfg,
    )
    return result, ledger


def test_happy_path_posts_one_marked_comment_with_an_honest_footer(tmp_path):
    rec, model = Recorder(), FakeAnthropic([suggest("bug")])
    result, _ = go(tmp_path, rec, model)
    assert result.result == "suggested" and result.label == "bug"
    assert len(rec.posts) == 1
    body = rec.posts[0]
    assert "Suggested label: **bug**" in body
    assert body.rstrip().endswith(MARKER)
    assert "stop bot" not in body
    assert "disable or delete the Surface Zero workflow" in body


def test_the_model_is_only_offered_the_default_label_vocabulary(tmp_path):
    rec, model = Recorder(), FakeAnthropic([suggest("bug")])
    go(tmp_path, rec, model)
    tools = model.messages.calls[0]["tools"]
    enum = tools[0]["input_schema"]["properties"]["label"]["enum"]
    assert enum == ["bug", "enhancement"]


def test_dry_run_records_the_suggestion_but_posts_nothing(tmp_path):
    rec, model = Recorder(), FakeAnthropic([suggest("enhancement")])
    env = base_env(tmp_path, **{"INPUT_DRY-RUN": "true"})
    result, ledger = go(tmp_path, rec, model, env=env)
    assert result.result == "dry-run" and result.label == "enhancement"
    assert rec.posts == []
    assert ledger.list_runs()[0].outbound_action.value == "shadow_recorded"


def test_abstention_posts_nothing(tmp_path):
    rec = Recorder()
    model = FakeAnthropic([FakeResponse(content=[ToolUseBlock(name="abstain", input={"reason": "unclear"})])])
    result, _ = go(tmp_path, rec, model)
    assert result.result == "abstained" and rec.posts == []


def test_a_rerun_does_not_comment_twice(tmp_path):
    rec = Recorder(comments=[{"body": "hello " + MARKER}])
    model = FakeAnthropic([suggest()])
    result, _ = go(tmp_path, rec, model)
    assert result.result == "skipped" and rec.posts == []
    assert model.messages.calls == []  # no model spend either


def test_other_event_types_are_skipped_with_the_fix(tmp_path):
    env = base_env(tmp_path, GITHUB_EVENT_NAME="push")
    result, _ = go(tmp_path, Recorder(), FakeAnthropic([suggest()]), env=env)
    assert result.result == "skipped" and "types: [opened]" in result.message


def test_model_failure_is_recorded_not_raised(tmp_path):
    rec, model = Recorder(), FakeAnthropic(error=RuntimeError("model down"))
    result, ledger = go(tmp_path, rec, model)
    assert result.result == "failed" and rec.posts == []
    assert ledger.list_runs()[0].status.value == "failed"


def test_permission_refusal_explains_the_fix(tmp_path):
    rec, model = Recorder(post_status=403), FakeAnthropic([suggest()])
    result, _ = go(tmp_path, rec, model)
    assert result.result == "failed" and "permissions: issues: write" in result.message


def test_label_read_denied_is_a_setup_error(tmp_path):
    def handler(request):
        return httpx.Response(403, json={"message": "no"})

    gh = ActionGitHub("t", client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"))
    with pytest.raises(ConfigError, match="permissions: issues: write"):
        gh.repo_labels(REPO)


# ------------------------------------------------------------------ output


def test_outputs_and_summary_and_record_are_written(tmp_path):
    rec, model = Recorder(), FakeAnthropic([suggest("bug")])
    env = base_env(tmp_path)
    result, ledger = go(tmp_path, rec, model, env=env)
    record = tmp_path / action.RECORD_FILENAME
    export_record(ledger, record)
    write_outputs(env, result, record)
    out = (tmp_path / "out.txt").read_text()
    assert "result=suggested" in out and "label=bug" in out and "run-record=" in out
    assert "suggested" in (tmp_path / "summary.md").read_text()
    data = json.loads(record.read_text())
    assert data["runs"][0]["llm_calls"], "the model call must be in the exported record"


# -------------------------------------------------------------- confidence


def test_threshold_zero_posts_regardless_of_confidence(tmp_path):
    rec = Recorder()
    result, _ = go(tmp_path, rec, FakeAnthropic([suggest("bug", confidence=0.1)]))
    assert result.result == "suggested" and len(rec.posts) == 1


def test_confidence_at_or_above_threshold_posts(tmp_path):
    rec = Recorder()
    env = base_env(tmp_path, **{"INPUT_CONFIDENCE-THRESHOLD": "0.7"})
    result, _ = go(tmp_path, rec, FakeAnthropic([suggest("bug", confidence=0.7)]), env=env)
    assert result.result == "suggested" and len(rec.posts) == 1


def test_confidence_below_threshold_posts_nothing_and_records_the_withheld_label(tmp_path):
    rec = Recorder()
    env = base_env(tmp_path, **{"INPUT_CONFIDENCE-THRESHOLD": "0.7"})
    result, ledger = go(tmp_path, rec, FakeAnthropic([suggest("bug", confidence=0.4)]), env=env)
    assert result.result == "abstained" and rec.posts == []
    stored = ledger.list_runs()[0].response
    assert stored["withheld_label"] == "bug" and stored["confidence"] == 0.4


@pytest.mark.parametrize("bad", [None, 1.7, -0.2])
def test_missing_or_out_of_range_confidence_fails_safe_when_a_threshold_is_set(tmp_path, bad):
    rec = Recorder()
    env = base_env(tmp_path, **{"INPUT_CONFIDENCE-THRESHOLD": "0.5"})
    result, _ = go(tmp_path, rec, FakeAnthropic([suggest("bug", confidence=bad)]), env=env)
    assert result.result == "abstained" and rec.posts == []


def test_the_confidence_field_is_required_in_the_tool_schema(tmp_path):
    model = FakeAnthropic([suggest()])
    go(tmp_path, Recorder(), model)
    schema = model.messages.calls[0]["tools"][0]["input_schema"]
    assert "confidence" in schema["required"]


# ------------------------------------------------------------ summary stats


def test_summary_reports_tokens_cost_and_confidence(tmp_path):
    result, ledger = go(tmp_path, Recorder(), FakeAnthropic([suggest("bug", confidence=0.83)]))
    line = run_stats(ledger, result, 0.0)
    assert "input tokens" in line and "output tokens" in line and "cost $" in line
    assert "confidence 0.83" in line and "threshold" not in line


def test_summary_shows_the_threshold_when_one_is_set(tmp_path):
    env = base_env(tmp_path, **{"INPUT_CONFIDENCE-THRESHOLD": "0.7"})
    result, ledger = go(tmp_path, Recorder(), FakeAnthropic([suggest("bug", confidence=0.4)]), env=env)
    line = run_stats(ledger, result, 0.7)
    assert result.result == "abstained" and "confidence 0.40" in line and "threshold 0.70" in line


def test_summary_says_plainly_when_no_model_call_was_made(tmp_path):
    rec = Recorder(comments=[{"body": MARKER}])
    result, ledger = go(tmp_path, rec, FakeAnthropic([suggest()]))
    assert run_stats(ledger, result) == "No model call was made."


def test_stats_line_lands_in_the_job_summary(tmp_path):
    env = base_env(tmp_path)
    result, ledger = go(tmp_path, Recorder(), FakeAnthropic([suggest("bug")]), env=env)
    write_outputs(env, result, None, run_stats(ledger, result))
    assert "cost $" in (tmp_path / "summary.md").read_text()


def test_summary_distinguishes_a_model_abstention_from_a_withheld_label(tmp_path):
    model = FakeAnthropic([FakeResponse(content=[ToolUseBlock(name="abstain", input={"reason": "unclear"})])])
    result, ledger = go(tmp_path, Recorder(), model)
    assert "the model chose to abstain" in run_stats(ledger, result)

    env = base_env(tmp_path, **{"INPUT_CONFIDENCE-THRESHOLD": "0.9"})
    result, ledger = go(tmp_path, Recorder(), FakeAnthropic([suggest("bug", confidence=0.5)]), env=env)
    line = run_stats(ledger, result, 0.9)
    assert "withheld 'bug'" in line and "the model chose to abstain" not in line
