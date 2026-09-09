"""Validated configuration keeps optional themes out of collection decisions."""

import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EXTRACTOR_VERSION = "2"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_url(value: str) -> str:
    # Unknown query parameters and path case may carry product information.
    parts = urlsplit(value)
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("Sources must be HTTP(S) URLs without embedded credentials")
    if parts.port not in {None, 80, 443}:
        raise ValueError("Only standard public HTTP(S) ports are supported")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: Literal["page", "json", "sitemap"] = "page"
    url: str
    interval_seconds: int = Field(default=3600, ge=60, le=604800)
    pinned: bool = False
    max_interval_seconds: int = Field(default=86400, ge=60, le=604800)
    max_pages: int = Field(default=1000, ge=1, le=20000)
    max_sitemaps: int = Field(default=100, ge=1, le=1000)
    include_prefixes: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=list)
    ignore_patterns: list[str] = Field(default_factory=list)
    prefer_markdown: bool = True

    _url = field_validator("url")(public_url)

    @field_validator("ignore_patterns")
    @classmethod
    def valid_patterns(cls, values: list[str]) -> list[str]:
        for pattern in values:
            re.compile(pattern)
        return values

    def identity(self) -> str:
        # Policy edits create an explicit new baseline, preserving the previous history.
        policy = self.model_dump(
            exclude={"id", "interval_seconds", "pinned", "max_interval_seconds"}
        )
        # Extraction upgrades must not manufacture changes against an incompatible baseline.
        policy["extractor_version"] = EXTRACTOR_VERSION
        return digest(canonical(policy).encode())


class Analysis(Strict):
    mode: Literal["always_deep", "manual_only"] = "always_deep"
    focus: list[str] = Field(default_factory=list)


class Protocol(Strict):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str
    profile: str = "Public protocol technical development and economic changes."
    enabled: bool = True
    sources: list[Source] = Field(min_length=1)
    analysis: Analysis = Field(default_factory=Analysis)

    @field_validator("sources")
    @classmethod
    def unique_sources(cls, sources: list[Source]) -> list[Source]:
        if len({s.id for s in sources}) != len(sources):
            raise ValueError("Source IDs must be unique within a protocol")
        return sources


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    database_url: SecretStr = SecretStr("")
    config_dir: Path = Path("config")
    blob_backend: Literal["local", "s3"] = "local"
    blob_dir: Path = Path("data/blobs")
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    aws_access_key_id: SecretStr = SecretStr("")
    aws_secret_access_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    openai_deep_model: str = "gpt-5.6-sol"
    openai_deep_reasoning_effort: str = "high"
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    collection_enabled: bool = False
    analysis_enabled: bool = False
    notifications_enabled: bool = False
    http_concurrency: int = Field(default=20, ge=1, le=100)
    per_host_concurrency: int = Field(default=2, ge=1, le=8)
    per_host_spacing_seconds: float = Field(default=0.5, ge=0.1)
    max_body_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)
    http_request_timeout_seconds: float = Field(default=60, ge=1, le=90)
    cluster_seconds: int = Field(default=600, ge=0, le=3600)
    analysis_chunk_chars: int = Field(default=24000, ge=4000, le=100000)
    analysis_max_chunks: int = Field(default=64, ge=1, le=500)
    worker_sleep_seconds: int = Field(default=30, ge=5, le=3600)


def load_protocols(root: Path) -> list[Protocol]:
    # A protocol name and source inventory are sufficient; no thesis file is mandatory.
    protocols = []
    for path in sorted((root / "protocols").glob("*.yaml")):
        body = yaml.safe_load(path.read_text())
        protocol = Protocol.model_validate(body)
        override = root / "analysis" / f"{protocol.id}.yaml"
        if override.exists():
            protocol.analysis = Analysis.model_validate(yaml.safe_load(override.read_text()))
        protocols.append(protocol)
    if not protocols:
        raise ValueError(f"No protocol configurations found in {root / 'protocols'}")
    if len({p.id for p in protocols}) != len(protocols):
        raise ValueError("Duplicate protocol IDs")
    return protocols
