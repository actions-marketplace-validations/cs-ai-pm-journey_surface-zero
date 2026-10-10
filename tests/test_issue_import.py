from surface_zero.issue_import import build_copy, neutralize, select_sets


def test_mentions_are_defused_so_nobody_is_notified():
    assert neutralize("thanks @someone for this") == "thanks `@someone` for this"
    assert neutralize("cc @org/team") == "cc `@org/team`"


def test_mentions_inside_code_are_left_alone():
    assert neutralize("run `git log @head` now") == "run `git log @head` now"
    fenced = "```\nuser@host @bob\n```"
    assert neutralize(fenced) == fenced


def test_email_is_removed_not_treated_as_a_mention():
    assert neutralize("write me at jane.doe@example.com please") == "write me at [email removed] please"


def test_bare_issue_references_are_defused():
    assert neutralize("same as #12 and #345.") == "same as `#12` and `#345`."


def test_cross_repo_references_and_issue_urls_are_defused_so_no_backlink_forms():
    out = neutralize("see Futsch1/medTimer#9 and https://github.com/Futsch1/medTimer/issues/9 ok")
    assert "`Futsch1/medTimer#9`" in out
    assert "`https://github.com/Futsch1/medTimer/issues/9`" in out
    other = neutralize("https://github.com/other/repo/pull/3")
    assert other == "`https://github.com/other/repo/pull/3`"


def test_images_and_attachments_are_removed():
    assert neutralize("![shot](https://x/y.png)") == "[image removed]"
    assert neutralize('<img src="https://x/y.png" width="3">') == "[image removed]"
    assert "[attachment removed]" in neutralize("log: https://github.com/user-attachments/files/123/log.txt")


def test_html_entity_is_not_mistaken_for_a_reference():
    assert neutralize("a &#39;b&#39; c") == "a &#39;b&#39; c"


def test_none_and_empty_bodies_are_safe():
    assert neutralize(None) == ""
    assert neutralize("") == ""


def test_build_copy_names_the_original_number_but_never_the_author():
    title, body = build_copy(
        {"number": 77, "title": "Crash @dev", "body": "see #5", "created_at": "2025-03-04T10:00:00Z",
         "author": "someuser", "user": {"login": "someuser"}}
    )
    assert title == "Crash `@dev`"
    assert "`#77`" in body and "2025-03-04" in body
    assert "someuser" not in body
    assert body.rstrip().endswith("see `#5`")


def _issue(n, created, assoc="NONE", state="closed", reason=None):
    return {"number": n, "created_at": created, "author_association": assoc, "state": state,
            "state_reason": reason}


def test_select_sets_picks_recent_outside_authored_plus_corrections_and_duplicate_children():
    issues = [
        _issue(1, "2024-01-01"), _issue(2, "2024-02-01"), _issue(3, "2024-03-01", assoc="OWNER"),
        _issue(4, "2024-04-01", state="open"), _issue(5, "2024-05-01"), _issue(6, "2024-06-01"),
    ]
    events = [{"issue": 1, "event": "unlabeled", "actor": "Futsch1", "label": "bug"}]
    sets = select_sets(issues, events, recent=2, duplicate_pairs={2: 1})
    assert sets["test"] == [1, 2, 5, 6]  # last two outside-authored, plus correction 1, plus dup child 2
    assert sets["corrections"] == [1]
    assert sets["duplicate_children"] == [2]
    assert 3 not in sets["test"]  # owner-authored is never a test issue
    assert sets["seed"] == [4]    # the only open issue that is not already a test issue


def test_select_sets_returns_oldest_first_and_originals_are_seeded():
    issues = [_issue(10, "2024-01-01"), _issue(11, "2024-09-01"), _issue(12, "2024-10-01")]
    sets = select_sets(issues, [], recent=1, duplicate_pairs={11: 10})
    assert sets["test"] == [11, 12]
    assert sets["seed"] == [10]


def test_importing_into_the_original_repo_or_its_owner_is_refused():
    import pytest

    from surface_zero.issue_import import UnsafeTargetError, assert_safe_target

    for bad in ("Futsch1/medTimer", "futsch1/medtimer", "Futsch1/anything", "no-slash"):
        with pytest.raises(UnsafeTargetError):
            assert_safe_target(bad)
    assert_safe_target("ForgeAS/medTimer-replay")


def test_original_title_uses_the_earliest_rename_source_not_the_current_title():
    from surface_zero.issue_import import original_title

    issue = {"number": 1, "title": "Show stock while editing dose"}
    events = [
        {"event": "renamed", "from": "Second title", "at": "2025-03-21T00:00:00Z"},
        {"event": "renamed", "from": "Stock count is broken", "at": "2025-03-20T12:00:00Z"},
        {"event": "labeled", "from": None, "at": "2025-03-20T01:00:00Z"},
    ]
    assert original_title(issue, events) == "Stock count is broken"


def test_original_title_falls_back_to_current_when_never_renamed():
    from surface_zero.issue_import import original_title

    assert original_title({"number": 1, "title": "Same"}, []) == "Same"


def test_build_copy_uses_the_title_override_and_still_neutralizes_it():
    title, _ = build_copy(
        {"number": 5, "title": "current", "body": "b", "created_at": "2025-01-01T00:00:00Z"},
        title="Filed as @someone",
    )
    assert title == "Filed as `@someone`"
