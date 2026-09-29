"""A repo's label vocabulary. The advisor is only ever allowed to choose from this.

Loaded from a JSON fixture during Monday/Tuesday build work because no external
maintainer has confirmed yet (see data/label_sets/fixture-repo.json). Once a
target is confirmed, the same shape gets populated from the GitHub API instead
of hand-typed, this loader doesn't care which source it came from.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class Label(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class LabelSet(BaseModel):
    repo: str
    labels: tuple[Label, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(label.name for label in self.labels)

    def __contains__(self, name: object) -> bool:
        return name in self.names

    @classmethod
    def from_file(cls, path: str | Path) -> LabelSet:
        data = json.loads(Path(path).read_text())
        return cls(repo=data["repo"], labels=tuple(Label(**l) for l in data["labels"]))
