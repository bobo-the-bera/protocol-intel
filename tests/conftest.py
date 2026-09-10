import os
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from protocol_intel.blobs import LocalBlobs
from protocol_intel.config import Analysis, Protocol, Settings, Source
from protocol_intel.database import Database


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        blob_dir=tmp_path / "blobs",
        cluster_seconds=0,
        telegram_bot_token="123456:TEST_ONLY",
        telegram_chat_id="-1001234567890",
    )


@pytest.fixture
def source():
    return Source(id="guide", url="https://docs.example.org/guide", prefer_markdown=False)


@pytest.fixture
def protocol(source):
    # Legacy integration cases explicitly exercise the retained always-deep path.
    return Protocol(
        id="test",
        name="Test protocol",
        profile="A public protocol.",
        sources=[source],
        analysis=Analysis(mode="always_deep"),
    )


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobs(tmp_path / "blobs")


@pytest.fixture
def public_dns(monkeypatch):
    # HTTP behavior is replayed locally; tests never crawl production websites.
    monkeypatch.setattr("protocol_intel.http.validate_destination", AsyncMock())


@pytest.fixture(scope="session")
def migrated_database():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Postgres tests run in CI or with TEST_DATABASE_URL configured")
    if not url.split("?")[0].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must name a disposable database ending in _test")
    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config("alembic.ini"), "head")
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if original is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original
    return url


@pytest.fixture
async def db(migrated_database):
    database = Database(migrated_database)
    await database.execute(
        "TRUNCATE protocols,sources,origins,storage_probes,reports,daily_digest_state,monitor_cycles,pilot_window CASCADE"
    )
    try:
        yield database
    finally:
        await database.close()
