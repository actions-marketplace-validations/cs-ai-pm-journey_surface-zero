"""Contract test for the Action's public interface.

action.yml is the product boundary tenants depend on. Changing an input or
output name, a default, or what runs is a breaking change and should be a
deliberate edit to this file, not a side effect of something else.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
META = yaml.safe_load((ROOT / "action.yml").read_text())

EXPECTED_INPUTS = {
    "anthropic-api-key": {"required": True, "default": None},
    "github-token": {"required": False, "default": "${{ github.token }}"},
    "labels": {"required": False, "default": ""},
    "confidence-threshold": {"required": False, "default": "0"},
    "duplicates": {"required": False, "default": "false"},
    "summaries": {"required": False, "default": "false"},
    "dry-run": {"required": False, "default": "false"},
}
EXPECTED_OUTPUTS = {"result", "label", "run-record"}


def test_required_top_level_metadata_is_present():
    for key in ("name", "description", "author", "branding", "inputs", "outputs", "runs"):
        assert META.get(key), f"action.yml is missing '{key}'"


def test_the_input_interface_is_exactly_the_frozen_one():
    assert set(META["inputs"]) == set(EXPECTED_INPUTS)
    for name, spec in EXPECTED_INPUTS.items():
        actual = META["inputs"][name]
        assert actual.get("description"), f"input '{name}' needs a description"
        assert bool(actual.get("required", False)) == spec["required"], name
        assert actual.get("default") == spec["default"], name


def test_defaults_are_strings_because_actions_inputs_always_are():
    for name, spec in META["inputs"].items():
        if "default" in spec:
            assert isinstance(spec["default"], str), name


def test_the_secret_input_has_no_default_so_it_can_never_be_baked_in():
    assert "default" not in META["inputs"]["anthropic-api-key"]


def test_outputs_are_exactly_the_frozen_ones():
    assert set(META["outputs"]) == EXPECTED_OUTPUTS


def test_it_is_a_docker_action_built_from_a_dockerfile_at_the_repo_root():
    runs = META["runs"]
    assert runs["using"] == "docker"
    image = runs["image"]
    assert image.endswith("Dockerfile"), "GitHub treats an image ending in 'Dockerfile' as a local build"
    assert "/" not in image, "the build context is the Dockerfile's directory; it must be the repo root"
    assert (ROOT / image).is_file()


def test_the_dockerfile_runs_the_real_entrypoint_module():
    text = (ROOT / META["runs"]["image"]).read_text()
    assert 'ENTRYPOINT ["python", "-m", "surface_zero.action"]' in text
    import importlib

    assert hasattr(importlib.import_module("surface_zero.action"), "main")


def test_no_secret_looking_value_is_in_the_metadata():
    raw = (ROOT / "action.yml").read_text()
    for needle in ("sk-ant", "ghp_", "github_pat_", "BEGIN PRIVATE KEY"):
        assert needle not in raw


def test_action_image_does_not_float_on_latest_tools():
    """A tenant's install must not change because an upstream tool released a new version."""
    dockerfile = (ROOT / "action.Dockerfile").read_text()
    assert ":latest" not in dockerfile
    assert "ghcr.io/astral-sh/uv:0.12.7" in dockerfile
