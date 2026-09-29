from surface_zero.labels import LabelSet


def test_loads_fixture_label_set():
    label_set = LabelSet.from_file("tests/fixtures/labels.json")
    assert label_set.repo == "fixture/example-repo"
    assert "bug" in label_set
    assert "not-a-real-label" not in label_set
    assert len(label_set.names) == len(label_set.labels)
