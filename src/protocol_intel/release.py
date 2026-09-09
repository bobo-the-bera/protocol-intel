"""An open requirement cannot become completed merely by documenting it."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from protocol_intel.config import Strict


class Requirement(Strict):
    id: str
    title: str
    gate: Literal["first_production", "full_specification", "scale"]
    status: Literal["open", "implemented_unverified", "verified"]
    owner: str
    specification: str
    acceptance: str
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence(self):
        if self.status == "verified" and not self.evidence:
            raise ValueError("Verified requirements must cite actual test/run evidence")
        return self


class ReleaseRegister(Strict):
    schema_version: Literal[1]
    requirements: list[Requirement] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.id for item in self.requirements}) != len(self.requirements):
            raise ValueError("Duplicate release requirement IDs")
        return self


def release_status(path: Path, target: str) -> dict:
    register = ReleaseRegister.model_validate(yaml.safe_load(path.read_text()))
    # Later gates include earlier requirements; there is no implicit deferral or bypass state.
    gates = ["first_production", "full_specification", "scale"]
    included = set(gates[: gates.index(target) + 1])
    blockers = [
        item.model_dump()
        for item in register.requirements
        if item.gate in included and item.status != "verified"
    ]
    return {"target": target, "ready": not blockers, "blockers": blockers}
