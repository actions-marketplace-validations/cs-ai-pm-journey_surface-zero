"""Wires the kill switch and a CommentPoster into an OutboundHandler.

This is the one place kill-switch semantics actually get enforced: checked
fresh on every call (no caching, see killswitch.py), and when active, the
poster is never called at all, not called-and-its-result-discarded. The
run_request/pipeline tests assert on the fake poster's call count for exactly
this reason, "verified, not just present" from the Block 11 design brief.
"""

from __future__ import annotations

from surface_zero.comment_poster import CommentPoster
from surface_zero.killswitch import KillSwitch
from surface_zero.issue_import import defuse_foreign_refs
from surface_zero.ledger import OutboundAction
from surface_zero.runner import AgentResult, OutboundResult, RunRequest


# GitHub's own close-reason vocabulary, rendered into plain English. "reopened"
# genuinely means "currently open" (state_reason is set even after a close/
# reopen cycle) so it is intentionally excluded here and falls through to the
# open-issue phrasing below.
_CLOSE_REASON_TEXT = {
    "completed": "closed as completed",
    "not_planned": "closed as not planned",
}


def render_comment(
    label: str,
    rationale: str,
    duplicate_of: int | None = None,
    duplicate_rationale: str | None = None,
    duplicate_of_state: str | None = None,
    duplicate_of_state_reason: str | None = None,
    repo: str | None = None,
) -> str:
    # Model-written text is defused before posting: it may echo @mentions or
    # cross-repo references from the issue it read, each of which has a real
    # side effect on someone else (a notification, or a backlink on another
    # repo's issue). The structured parts we generate ourselves are untouched.
    rationale = defuse_foreign_refs(rationale, repo)
    if duplicate_rationale:
        duplicate_rationale = defuse_foreign_refs(duplicate_rationale, repo)
    if duplicate_of is None:
        duplicate_line = ""
    elif duplicate_of_state == "closed":
        # Deliberately more visible than the open-issue phrasing: a match
        # against a closed issue is a possible regression of already-
        # resolved work, not just "here's a related thread." See
        # candidate_fetcher.py's module docstring for why this exists.
        reason_text = _CLOSE_REASON_TEXT.get(duplicate_of_state_reason, "closed")
        duplicate_line = (
            f"\n\n⚠️ Possible duplicate of **closed** #{duplicate_of} ({reason_text}) "
            f"-- this may be a regression: {duplicate_rationale}"
        )
    else:
        duplicate_line = f"\n\nPossible duplicate of #{duplicate_of}: {duplicate_rationale}"
    return (
        f"Suggested label: **{label}**\n\n{rationale}{duplicate_line}\n\n"
        "---\n"
        "_Posted by an advisory-only bot. It can't apply this label, only "
        "suggest it, comment `stop bot` any time to turn it off before its "
        "next run._"
    )


def build_outbound_handler(
    poster: CommentPoster,
    kill_switch: KillSwitch,
    shadow_repos: frozenset[str] = frozenset(),
):
    """shadow_repos: repos (case-insensitive) where the agent runs and the
    ledger records its recommendation but the poster is never called. Checked
    before the kill switch because shadow is the more specific state: the
    comment was never going to be posted, so reporting it as a kill-switch
    suppression would misattribute the cause.
    """
    shadow = frozenset(r.lower() for r in shadow_repos)

    def handler(request: RunRequest, result: AgentResult) -> OutboundResult:
        if request.target and request.target.lower() in shadow:
            return OutboundResult(action=OutboundAction.SHADOW_RECORDED)

        status = kill_switch.status()
        if status.active:
            return OutboundResult(
                action=OutboundAction.COMMENT_SUPPRESSED,
                response_patch={"kill_switch_reason": status.reason, "kill_switch_source": status.source},
            )

        label = result.response.get("suggested_label")
        rationale = result.response.get("rationale", "")
        duplicate_of = result.response.get("duplicate_of")
        duplicate_rationale = result.response.get("duplicate_rationale")
        duplicate_of_state = result.response.get("duplicate_of_state")
        duplicate_of_state_reason = result.response.get("duplicate_of_state_reason")
        issue_number = request.payload.get("number")
        if not request.target or not label or issue_number is None:
            # Nothing sane to post. Fails loudly rather than posting a broken
            # comment or silently doing nothing while claiming success.
            raise ValueError(
                f"cannot post a comment: target={request.target!r} "
                f"label={label!r} issue_number={issue_number!r}"
            )

        url = poster.post_comment(
            repo=request.target,
            issue_number=issue_number,
            body=render_comment(
                label,
                rationale,
                duplicate_of,
                duplicate_rationale,
                duplicate_of_state,
                duplicate_of_state_reason,
                repo=request.target,
            ),
        )
        return OutboundResult(
            action=OutboundAction.COMMENT_POSTED,
            response_patch={"comment_url": url},
        )

    return handler
