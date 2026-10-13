"""GitHub Action entrypoint: a second front door onto the same Surface Zero core.

The hosted service receives a webhook; this module reads the Actions
environment instead (INPUT_* variables, GITHUB_EVENT_PATH, GITHUB_TOKEN) and
builds the same RunRequest, then hands it to the same advisor, outbound
handler, comment renderer and ledger. Nothing here re-implements triage.

Two differences from the hosted mode are deliberate and are documented in
b12w2-packaging-adr.md:

* The ledger is a throwaway SQLite file. The run record is exported to
  `surface-zero-run.json` in the workspace and summarised in the job summary.
* The workflow token's permissions cannot be read back, so the permission
  half of the scope check is skipped (scope_policy="workflow"). The tenant's
  `permissions:` block is the control, and the setup errors say so.

Exit codes: a setup/configuration problem exits 1 with a message that says
what to do next (fix once). A run-time failure (model or GitHub API down) is
recorded in the ledger and exits 0 with a warning, so an outage does not turn
every new issue's workflow red. An unexpected bug exits 1 on purpose.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from surface_zero.advisor import Advisor
from surface_zero.comment_poster import CommentPoster, GitHubCommentPoster
from surface_zero.killswitch import KillSwitch
from surface_zero.labels import Label, LabelSet
from surface_zero.ledger import Ledger, OutboundAction, RunStatus
from surface_zero.pipeline import build_outbound_handler
from surface_zero.runner import RunRequest, run_request

GITHUB_API = "https://api.github.com"
MARKER = "<!-- surface-zero-action:v1 -->"
FOOTER = (
    "_Posted by the Surface Zero Action. It can't apply this label, only suggest it. "
    "To turn it off, disable or delete the Surface Zero workflow in this repository._"
)
# Labels that record a maintainer's closing decision. An advisory bot should not
# suggest them, so they are left out of the default vocabulary. A tenant can
# still list them explicitly in `labels`.
EXCLUDED_DEFAULT_LABELS = frozenset({"duplicate", "invalid", "wontfix"})
RECORD_FILENAME = "surface-zero-run.json"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ConfigError(Exception):
    """A setup problem the maintainer can fix. The message says how."""


@dataclass(frozen=True)
class ActionConfig:
    api_key: str
    github_token: str
    labels: tuple[str, ...]
    confidence_threshold: float
    duplicates: bool
    summaries: bool
    dry_run: bool


@dataclass(frozen=True)
class IssueEvent:
    repo: str
    number: int
    title: str
    body: str
    event_name: str
    action: str


@dataclass(frozen=True)
class ActionResult:
    result: str  # suggested | dry-run | abstained | skipped | failed
    message: str
    label: str | None = None
    run_id: str | None = None
    comment_url: str | None = None


# ---------------------------------------------------------------- configuration


def _input(env: Mapping[str, str], name: str) -> str:
    """Action inputs arrive as INPUT_<NAME>; hyphens are kept by the runner,
    but be tolerant of an underscore spelling."""
    for key in (f"INPUT_{name.upper()}", f"INPUT_{name.upper().replace('-', '_')}"):
        if key in env:
            return env[key].strip()
    return ""


def _parse_bool(name: str, raw: str, default: bool) -> bool:
    if raw == "":
        return default
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    raise ConfigError(f"Input '{name}' must be 'true' or 'false', got '{raw}'.")


def load_config(env: Mapping[str, str]) -> ActionConfig:
    api_key = _input(env, "anthropic-api-key")
    if not api_key:
        raise ConfigError(
            "Input 'anthropic-api-key' is empty. Add your Anthropic API key as a repository "
            "secret named ANTHROPIC_API_KEY (Settings > Secrets and variables > Actions), then "
            "pass it in the workflow: anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}"
        )
    token = _input(env, "github-token") or env.get("GITHUB_TOKEN", "")
    if not token:
        raise ConfigError(
            "No GitHub token available. Leave 'github-token' at its default "
            "(${{ github.token }}) and give the job: permissions: issues: write"
        )

    raw_threshold = _input(env, "confidence-threshold")
    try:
        threshold = float(raw_threshold) if raw_threshold else 0.0
    except ValueError:
        raise ConfigError(
            f"Input 'confidence-threshold' must be a number between 0 and 1, got '{raw_threshold}'."
        ) from None
    if not 0.0 <= threshold <= 1.0:
        raise ConfigError(
            f"Input 'confidence-threshold' must be between 0 and 1, got {threshold}."
        )

    duplicates = _parse_bool("duplicates", _input(env, "duplicates"), False)
    summaries = _parse_bool("summaries", _input(env, "summaries"), False)
    dry_run = _parse_bool("dry-run", _input(env, "dry-run"), False)

    # Accepted in the interface now, enforced as the features land. Until then
    # asking for them fails loudly: an input that is silently ignored would be
    # a lie about what the Action does.
    not_yet = []
    if threshold > 0.0:
        not_yet.append("confidence-threshold above 0")
    if duplicates:
        not_yet.append("duplicates: true")
    if summaries:
        not_yet.append("summaries: true")
    if not_yet:
        raise ConfigError(
            "Not available in this version: " + ", ".join(not_yet) + ". "
            "Remove the input or leave it at its default."
        )

    names = tuple(n.strip() for n in _input(env, "labels").split(",") if n.strip())
    return ActionConfig(
        api_key=api_key,
        github_token=token,
        labels=names,
        confidence_threshold=threshold,
        duplicates=duplicates,
        summaries=summaries,
        dry_run=dry_run,
    )


def load_event(env: Mapping[str, str]) -> IssueEvent:
    repo = env.get("GITHUB_REPOSITORY", "")
    if not _REPO_RE.match(repo):
        raise ConfigError(f"GITHUB_REPOSITORY is missing or malformed ('{repo}'). Run this as a GitHub Action.")
    path = env.get("GITHUB_EVENT_PATH", "")
    if not path or not Path(path).is_file():
        raise ConfigError("GITHUB_EVENT_PATH is missing. This must run inside a GitHub Actions workflow.")
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"The event payload could not be read: {exc}") from exc

    event_name = env.get("GITHUB_EVENT_NAME", "")
    action = payload.get("action", "")
    payload_repo = (payload.get("repository") or {}).get("full_name")
    if payload_repo and payload_repo.lower() != repo.lower():
        raise ConfigError(
            f"Event repository '{payload_repo}' does not match GITHUB_REPOSITORY '{repo}'. Refusing to run."
        )
    issue = payload.get("issue")
    if event_name == "issues" and action == "opened":
        if not isinstance(issue, dict) or not isinstance(issue.get("number"), int):
            raise ConfigError("The 'issues' event payload has no usable issue number.")
        return IssueEvent(
            repo=repo,
            number=issue["number"],
            title=issue.get("title") or "",
            body=issue.get("body") or "",
            event_name=event_name,
            action=action,
        )
    return IssueEvent(repo=repo, number=0, title="", body="", event_name=event_name, action=action)


# ----------------------------------------------------------------------- GitHub


class ActionGitHub:
    """The two read calls the Action needs besides posting: the repo's labels
    and whether this Action has already commented on an issue."""

    def __init__(self, token: str, *, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            base_url=GITHUB_API,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15.0,
        )
        self._headers = {"Authorization": f"Bearer {token}"}

    def _get_pages(self, path: str, max_pages: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            response = self._client.get(
                path, params={"per_page": 100, "page": page}, headers=self._headers
            )
            if response.status_code in (401, 403, 404):
                raise ConfigError(
                    f"GitHub returned {response.status_code} for {path}. Check that the job has "
                    "'permissions: issues: write' and that github-token is the default ${{ github.token }}."
                )
            response.raise_for_status()
            batch = response.json()
            items.extend(batch)
            if len(batch) < 100:
                break
        return items

    def repo_labels(self, repo: str) -> list[dict[str, Any]]:
        return self._get_pages(f"/repos/{repo}/labels", max_pages=5)

    def has_marker_comment(self, repo: str, number: int, marker: str) -> bool:
        comments = self._get_pages(f"/repos/{repo}/issues/{number}/comments", max_pages=3)
        return any(marker in (c.get("body") or "") for c in comments)


def build_label_set(repo: str, repo_labels: list[dict[str, Any]], requested: tuple[str, ...]) -> LabelSet:
    by_name = {l["name"]: l for l in repo_labels}
    if requested:
        unknown = [n for n in requested if n not in by_name]
        if unknown:
            raise ConfigError(
                f"Label(s) {unknown} in the 'labels' input do not exist in {repo}. "
                f"Existing labels: {sorted(by_name)}. Create them or fix the list."
            )
        chosen = [by_name[n] for n in requested]
    else:
        chosen = [l for l in repo_labels if l["name"].lower() not in EXCLUDED_DEFAULT_LABELS]
    if len(chosen) < 2:
        raise ConfigError(
            f"{repo} has {len(chosen)} usable label(s); at least 2 are needed to suggest anything useful. "
            "Create a few labels (for example bug and enhancement) or list some in the 'labels' input."
        )
    return LabelSet(
        repo=repo,
        labels=tuple(Label(name=l["name"], description=l.get("description") or "") for l in chosen),
    )


class MarkedPoster:
    """Adds a hidden marker so a later run can tell it already commented."""

    def __init__(self, inner: CommentPoster):
        self._inner = inner

    def post_comment(self, *, repo: str, issue_number: int, body: str) -> str:
        return self._inner.post_comment(repo=repo, issue_number=issue_number, body=body + "\n\n" + MARKER)


# -------------------------------------------------------------------------- run


def run(
    env: Mapping[str, str],
    *,
    github: ActionGitHub,
    advisor_factory,
    poster: CommentPoster,
    ledger: Ledger,
    config: ActionConfig | None = None,
) -> ActionResult:
    config = config or load_config(env)
    event = load_event(env)

    if not (event.event_name == "issues" and event.action == "opened"):
        return ActionResult(
            "skipped",
            f"Not an issue-opened event (event '{event.event_name}', action '{event.action}'). "
            "Trigger this workflow with: on: issues: types: [opened]",
        )
    if github.has_marker_comment(event.repo, event.number, MARKER):
        return ActionResult("skipped", f"Surface Zero already commented on #{event.number}; not commenting again.")

    label_set = build_label_set(event.repo, github.repo_labels(event.repo), config.labels)
    advisor = advisor_factory(label_set)
    handler = build_outbound_handler(
        MarkedPoster(poster),
        KillSwitch(Path(tempfile.gettempdir()) / "surface-zero-no-kill-switch.json"),
        shadow_repos=frozenset({event.repo}) if config.dry_run else frozenset(),
        footer=FOOTER,
    )
    key = f"action:{event.repo}#{event.number}:{env.get('GITHUB_RUN_ID', '0')}:{env.get('GITHUB_RUN_ATTEMPT', '1')}"
    outcome = run_request(
        RunRequest(
            idempotency_key=key,
            event_type="issues.opened",
            target=event.repo,
            payload={"title": event.title, "body": event.body, "number": event.number},
        ),
        advisor,
        ledger,
        handler,
    )
    run_rec = outcome.run
    response = run_rec.response or {}

    if run_rec.status == RunStatus.FAILED:
        hint = ""
        if "403" in (run_rec.error_message or ""):
            hint = (
                " GitHub refused the comment. Give the job 'permissions: issues: write' "
                "(and leave github-token at its default)."
            )
        return ActionResult(
            "failed",
            f"The run failed ({run_rec.error_type}: {run_rec.error_message}). Nothing was posted.{hint}",
            run_id=run_rec.run_id,
        )
    if run_rec.status == RunStatus.ABSTAINED:
        return ActionResult("abstained", "The advisor abstained; nothing was posted.", run_id=run_rec.run_id)
    label = response.get("suggested_label")
    if run_rec.outbound_action == OutboundAction.SHADOW_RECORDED:
        return ActionResult(
            "dry-run", f"Dry run: would have suggested '{label}'. Nothing was posted.", label=label, run_id=run_rec.run_id
        )
    return ActionResult(
        "suggested",
        f"Suggested '{label}' on #{event.number}.",
        label=label,
        run_id=run_rec.run_id,
        comment_url=response.get("comment_url"),
    )


# ----------------------------------------------------------------------- output


def export_record(ledger: Ledger, path: Path) -> None:
    runs = []
    for r in ledger.list_runs():
        d = r.model_dump(mode="json")
        d["llm_calls"] = [c.model_dump(mode="json") for c in ledger.llm_calls_for_run(r.run_id)]
        runs.append(d)
    path.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")


def write_outputs(env: Mapping[str, str], result: ActionResult, record_path: Path | None) -> None:
    out = env.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"result={result.result}\n")
            f.write(f"label={result.label or ''}\n")
            f.write(f"run-record={record_path or ''}\n")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Surface Zero\n\n**{result.result}**: {result.message}\n")


def main(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        config = load_config(env)
        import anthropic

        workspace = Path(env.get("GITHUB_WORKSPACE") or ".")
        ledger = Ledger(Path(tempfile.mkdtemp(prefix="surface-zero-")) / "ledger.db")
        client = anthropic.Anthropic(api_key=config.api_key)
        result = run(
            env,
            github=ActionGitHub(config.github_token),
            advisor_factory=lambda label_set: Advisor(client, label_set),
            poster=GitHubCommentPoster(config.github_token, scope_policy="workflow"),
            ledger=ledger,
            config=config,
        )
        record_path = workspace / RECORD_FILENAME
        export_record(ledger, record_path)
    except ConfigError as exc:
        print(f"::error title=Surface Zero setup::{exc}")
        return 1
    write_outputs(env, result, record_path)
    if result.result == "failed":
        print(f"::warning title=Surface Zero::{result.message}")
    else:
        print(result.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
