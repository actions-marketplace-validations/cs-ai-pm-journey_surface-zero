"""Turns a real repo's public issue into a safe test fixture on a fork.

Used only by the MedTimer fork replay (research/scripts/medtimer_*.py). The point of
the fork is that NOTHING touches the original repo or its people. Pasting
public issue text into another repo is not neutral by default, because GitHub
turns certain text into side effects:

* ``@name`` notifies that real user.
* ``#123``, ``owner/repo#123`` and issue/PR URLs create cross-reference
  events on the target issue ("mentioned this issue in ...").
* Images and attachment links expose content the reporter did not post here.
* Email addresses are personal data.

neutralize() removes each of those effects while keeping the text readable
enough for the advisor to classify. Anything inside a fenced code block or
an inline code span is left alone, because GitHub does not link or notify
inside code. Wrapping in a code span is how we defuse everything else.
"""

from __future__ import annotations

import re
from typing import Any

ORIGINAL_REPO = "Futsch1/medTimer"

_FENCE = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)
_INLINE_CODE = re.compile(r"(`[^`\n]*`)")

_IMG_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_IMG_HTML = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTACHMENT = re.compile(
    r"\[[^\]]*\]\(https?://github\.com/(?:user-attachments|[^)\s]*/files)/[^)]*\)"
    r"|https?://github\.com/user-attachments/\S+"
    r"|https?://user-images\.githubusercontent\.com/\S+",
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Any github.com issue/PR/discussion/commit URL: these generate backlinks.
_GH_URL = re.compile(r"https?://(?:www\.)?github\.com/[^\s)>\]]+/(?:issues|pull|discussions|commit)/[^\s)>\]]+")
# owner/repo#N, and also the fork-network shorthand owner#N, which GitHub
# autolinks to the parent repo when the target repo is a fork of it (seen
# 2026-10-12: a bot comment on the replay fork rendered a live link to the
# original issue). Bare #N has no prefix and is not matched.
_CROSS_REF = re.compile(r"(?<![\w`/&])([A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9_.-]+)?)#(\d+)\b")
_BARE_REF = re.compile(r"(?<![\w`/&])#(\d+)\b")
_MENTION = re.compile(r"(?<![\w`/@])@([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:/[A-Za-z0-9_.-]+)?)")


def _neutralize_plain(text: str) -> str:
    text = _IMG_MD.sub("[image removed]", text)
    text = _IMG_HTML.sub("[image removed]", text)
    text = _ATTACHMENT.sub("[attachment removed]", text)
    text = _EMAIL.sub("[email removed]", text)
    text = _GH_URL.sub(lambda m: f"`{m.group(0)}`", text)
    text = _CROSS_REF.sub(lambda m: f"`{m.group(1)}#{m.group(2)}`", text)
    text = _BARE_REF.sub(lambda m: f"`#{m.group(1)}`", text)
    text = _MENTION.sub(lambda m: f"`@{m.group(1)}`", text)
    return text


def neutralize(text: str | None) -> str:
    if not text:
        return ""
    out: list[str] = []
    for chunk in _FENCE.split(text):
        if _FENCE.fullmatch(chunk):
            out.append(chunk)  # fenced code is inert on GitHub
            continue
        for piece in _INLINE_CODE.split(chunk):
            out.append(piece if _INLINE_CODE.fullmatch(piece) else _neutralize_plain(piece))
    return "".join(out)


def original_title(issue: dict[str, Any], rename_events: list[dict[str, Any]]) -> str:
    """The title the issue was filed with. GitHub records each retitle as a
    ``renamed`` event carrying the previous title; the earliest event's
    ``from`` is the title at filing. The current title can encode a later
    maintainer judgment (e.g. a bug retitled as a feature request) and would
    leak the answer to the advisor. Falls back to the current title when the
    issue was never renamed. Body edits leave no event, so they cannot be
    recovered; that limitation is documented in the pre-registration.
    """
    renames = sorted(
        (e for e in rename_events if e.get("event") == "renamed" and e.get("from") is not None),
        key=lambda e: e["at"],
    )
    return renames[0]["from"] if renames else issue["title"]


def build_copy(issue: dict[str, Any], title: str | None = None) -> tuple[str, str]:
    """(title, body) for the fork copy. The original author is never named.
    Pass ``title`` to override the issue's current title (see original_title)."""
    number = issue["number"]
    header = (
        f"> Replay fixture: copy of public issue `#{number}` from the original "
        f"repository, opened {issue['created_at'][:10]}. Mentions, links, images, "
        "attachments and emails were removed or defused. The original author is "
        "not named. Nothing here is posted to or linked from the original repo.\n\n"
    )
    return neutralize(title if title is not None else issue["title"]), header + neutralize(issue.get("body"))


def select_sets(
    issues: list[dict[str, Any]],
    label_events: list[dict[str, Any]],
    *,
    recent: int = 60,
    owner: str = "Futsch1",
    duplicate_pairs: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Which issues are replayed with the bot (``test``) and which are seeded
    first without it (``seed``) so duplicate matching has real originals.

    test = the most recent ``recent`` outside-authored issues
           + issues where the owner removed a label (maintainer corrections)
           + issues closed as a duplicate whose original is known.
    seed = the originals of those known duplicate pairs, plus every other
           open issue not in test, so candidates look like a live backlog.

    Both are returned in ascending creation order: replay oldest first.
    """
    duplicate_pairs = duplicate_pairs or {}
    by_number = {i["number"]: i for i in issues}

    outside = sorted(
        (i for i in issues if i["author_association"] != "OWNER"),
        key=lambda i: i["created_at"],
    )
    test_numbers = {i["number"] for i in outside[-recent:]}

    corrections = {
        e["issue"] for e in label_events
        if e["event"] == "unlabeled" and e["actor"] == owner and e["issue"] in by_number
        and by_number[e["issue"]]["author_association"] != "OWNER"
    }
    dup_children = {c for c in duplicate_pairs if c in by_number}
    test_numbers |= corrections | dup_children

    seed_numbers = {o for o in duplicate_pairs.values() if o in by_number} - test_numbers
    seed_numbers |= {i["number"] for i in issues if i["state"] == "open"} - test_numbers

    def ordered(numbers):
        return [n for n, _ in sorted(((n, by_number[n]["created_at"]) for n in numbers), key=lambda t: t[1])]

    return {
        "test": ordered(test_numbers),
        "seed": ordered(seed_numbers),
        "corrections": sorted(corrections),
        "duplicate_children": sorted(dup_children),
    }


class UnsafeTargetError(Exception):
    """The import was pointed somewhere it must never write."""


def assert_safe_target(repo: str) -> None:
    """The whole point of the fork is that nothing touches the original repo
    or its owner's account. Refuse the original repo, and refuse anything under
    the original owner's namespace, regardless of how the caller got here.
    """
    owner = ORIGINAL_REPO.split("/")[0].lower()
    if "/" not in repo or repo.lower() == ORIGINAL_REPO.lower() or repo.split("/")[0].lower() == owner:
        raise UnsafeTargetError(f"refusing to import into {repo!r}: that is the original repo or its owner")


def defuse_foreign_refs(text: str | None, own_repo: str | None) -> str:
    """Make model-written comment text safe to post.

    Removes the side effects the bot itself could cause by repeating text it
    read: @mentions notify real users, and references or URLs pointing at any
    OTHER repository create cross-reference entries on that repository's
    issues. References to ``own_repo`` are left alone, as are bare ``#N``
    references (they can only point inside the repo being commented on) and
    anything already inside a code span or fenced block.
    """
    if not text:
        return ""
    own = (own_repo or "").lower()

    def ref(m):
        target = m.group(1).lower()
        if "/" in target and target == own:
            return m.group(0)
        return f"`{m.group(1)}#{m.group(2)}`"

    def url(m):
        found = m.group(0)
        parts = found.split("github.com/", 1)[1].split("/")
        if len(parts) >= 2 and f"{parts[0]}/{parts[1]}".lower() == own:
            return found
        return f"`{found}`"

    def plain(chunk: str) -> str:
        chunk = _GH_URL.sub(url, chunk)
        chunk = _CROSS_REF.sub(ref, chunk)
        chunk = _MENTION.sub(lambda m: f"`@{m.group(1)}`", chunk)
        return chunk

    out: list[str] = []
    for part in _FENCE.split(text):
        if _FENCE.fullmatch(part):
            out.append(part)
            continue
        for piece in _INLINE_CODE.split(part):
            out.append(piece if _INLINE_CODE.fullmatch(piece) else plain(piece))
    return "".join(out)
