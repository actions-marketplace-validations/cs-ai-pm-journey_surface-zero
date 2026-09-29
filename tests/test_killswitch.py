import json

import pytest

from surface_zero.killswitch import KillSwitch


@pytest.fixture
def switch(tmp_path):
    return KillSwitch(tmp_path / "kill_switch.json")


def test_inactive_by_default_when_no_file_and_no_env(switch, monkeypatch):
    monkeypatch.delenv("SURFACE_ZERO_KILL", raising=False)
    status = switch.status()
    assert status.active is False
    assert status.source is None


def test_engage_writes_a_file_the_status_call_reads_fresh(switch, monkeypatch):
    monkeypatch.delenv("SURFACE_ZERO_KILL", raising=False)
    assert switch.is_active() is False
    switch.engage("maintainer asked to pause")
    assert switch.is_active() is True
    status = switch.status()
    assert status.reason == "maintainer asked to pause"
    assert status.source == "file"


def test_disengage_reverses_it(switch, monkeypatch):
    monkeypatch.delenv("SURFACE_ZERO_KILL", raising=False)
    switch.engage("testing")
    assert switch.is_active() is True
    switch.disengage()
    assert switch.is_active() is False


def test_env_var_overrides_regardless_of_file_state(switch, monkeypatch):
    switch.disengage()
    monkeypatch.setenv("SURFACE_ZERO_KILL", "1")
    status = switch.status()
    assert status.active is True
    assert status.source == "env"


def test_env_var_of_zero_does_not_count_as_set(switch, monkeypatch):
    monkeypatch.setenv("SURFACE_ZERO_KILL", "0")
    assert switch.is_active() is False


def test_unreadable_kill_switch_file_fails_safe_as_active(switch, monkeypatch):
    monkeypatch.delenv("SURFACE_ZERO_KILL", raising=False)
    switch.path.parent.mkdir(parents=True, exist_ok=True)
    switch.path.write_text("{not valid json")
    status = switch.status()
    assert status.active is True
    assert "unreadable" in status.reason


def test_no_status_call_caches_a_stale_reading(switch, monkeypatch):
    monkeypatch.delenv("SURFACE_ZERO_KILL", raising=False)
    assert switch.is_active() is False
    switch.engage("flip mid-session")
    assert switch.is_active() is True  # a fresh call sees the flip immediately
