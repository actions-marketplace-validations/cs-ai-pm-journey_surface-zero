from surface_zero.labels import LabelSet


def test_loads_fixture_label_set():
    label_set = LabelSet.from_file("tests/fixtures/labels.json")
    assert label_set.repo == "fixture/example-repo"
    assert "bug" in label_set
    assert "not-a-real-label" not in label_set
    assert len(label_set.names) == len(label_set.labels)


def test_nuts_label_set_loads_with_only_the_agreed_type_labels():
    from surface_zero.labels import LabelSet

    ls = LabelSet.from_file("data/label_sets/nuts.json")
    assert ls.repo == "thevpc/nuts"
    assert set(ls.names) == {"bug", "enhancement", "api-feature", "app", "architecture"}
    assert not any(n.startswith("size-") for n in ls.names)


def test_medtimer_replay_label_set_offers_only_the_two_labels_the_maintainer_uses():
    from surface_zero.labels import LabelSet

    ls = LabelSet.from_file("data/label_sets/medtimer-replay.json")
    assert ls.repo == "ForgeAS/medTimer-replay"
    assert set(ls.names) == {"bug", "enhancement"}
