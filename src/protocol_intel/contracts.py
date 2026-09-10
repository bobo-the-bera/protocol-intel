"""Shared report contracts keep model routing independent of delivery and storage."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    importance: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    observed_change: str
    significance: str
    evidence: list[str]
    uncertainty: str


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[Finding]
    nonmaterial_summary: str
