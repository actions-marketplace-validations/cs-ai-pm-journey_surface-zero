"""The kill switch. A governance primitive, built alongside the agent, not after.

Two independent ways to trip it, on purpose:

* A file at `data/kill_switch.json`, so a human editing the repo (or a future
  ops runbook step) can flip it durably and it survives a process restart.
* An environment variable, SURFACE_ZERO_KILL, so an operator with only shell
  access to the running process (no repo write access, no deploy pipeline)
  can kill it in an emergency without touching disk. Checked every call, no
  caching, because a kill switch that needs a restart to take effect isn't one.

Either source being active is enough. This only gates the outbound comment,
never the model call, per the Block 11 design brief: disabling the outbound
action specifically, not just stopping the agent from thinking, is what lets
you keep observing behavior (via the ledger) while making it externally inert.

"Verified, not just present" (also from the brief) means: this module's own
test suite has to prove that when active, no comment-posting call happens at
all, not that a flag was merely read. See tests/test_killswitch.py and the
pipeline test that checks the fake poster is never called while this is on.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = "data/kill_switch.json"
ENV_VAR = "SURFACE_ZERO_KILL"


@dataclass(frozen=True)
class KillSwitchStatus:
    active: bool
    reason: str | None
    source: str | None  # "file", "env", or None when inactive


class KillSwitch:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)

    def status(self) -> KillSwitchStatus:
        env_value = os.environ.get(ENV_VAR)
        if env_value and env_value != "0":
            return KillSwitchStatus(active=True, reason=f"{ENV_VAR} set", source="env")

        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                # A kill switch file that fails to parse must fail SAFE, that means
                # treated as active, not silently ignored. An operator who wrote a
                # bad JSON file was almost certainly trying to turn the bot off.
                return KillSwitchStatus(
                    active=True, reason=f"kill switch file unreadable: {exc}", source="file"
                )
            if data.get("active"):
                return KillSwitchStatus(
                    active=True, reason=data.get("reason"), source="file"
                )

        return KillSwitchStatus(active=False, reason=None, source=None)

    def is_active(self) -> bool:
        return self.status().active

    def engage(self, reason: str) -> None:
        """Convenience for an ops runbook step or a test. Writes the file source."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"active": True, "reason": reason}))

    def disengage(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"active": False, "reason": None}))
