import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import yaml

from protocol_intel import cli
from protocol_intel.pilot import pilot_status


@pytest.mark.parametrize("expired", [False, True])
def test_workflow_disables_only_after_database_expiry(monkeypatch, expired):
    workflow = yaml.safe_load(Path(".github/workflows/monitor.yml").read_text())
    job = workflow["jobs"]["monitor"]
    assert job["env"]["PILOT_HOURS"] == "72"
    assert job["permissions"]["actions"] == "write"
    step = job["steps"][-1]
    assert step["if"] == "always()"
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/test-repository")
    monkeypatch.setenv("GH_STOP_TOKEN", "TEST_ONLY_TOKEN")
    query = Mock(return_value=json.dumps({"started": True, "expired": expired}))
    monkeypatch.setattr("subprocess.check_output", query)
    response = MagicMock()
    response.__enter__.return_value.status = 204
    send = Mock(return_value=response)
    monkeypatch.setattr("urllib.request.urlopen", send)
    exec(compile(step["run"], "<workflow-disable-step>", "exec"), {})  # noqa: S102
    assert query.call_args.args[0] == ["uv", "run", "protocol-intel", "pilot-status"]
    assert send.call_count == int(expired)
    if expired:
        request = send.call_args.args[0]
        assert request.method == "PUT"
        assert (
            request.full_url
            == "https://api.github.com/repos/owner/test-repository/actions/workflows/monitor.yml/disable"
        )


@pytest.mark.postgres
async def test_pilot_clock_is_read_only_until_start_and_never_extends(db):
    assert await pilot_status(db) == {"started": False, "expired": False}
    first = await pilot_status(db, 72)
    again = await pilot_status(db, 72)
    assert first["started_at"] == again["started_at"]
    assert first["ends_at"] == again["ends_at"]
    assert 259190 < again["remaining_seconds"] <= 259200
    await db.execute(
        "UPDATE pilot_window SET started_at=now()-interval '73 hours',ends_at=now()-interval '1 hour'"
    )
    assert (await pilot_status(db, 72))["expired"]


@pytest.mark.postgres
async def test_concurrent_starts_have_one_shared_deadline(db):
    results = await asyncio.gather(*(pilot_status(db, 72) for _ in range(3)))
    assert len({r["ends_at"] for r in results}) == 1


@pytest.mark.postgres
async def test_expired_pilot_cannot_start_work_even_if_setting_removed(
    db, blobs, settings, monkeypatch
):
    await pilot_status(db, 72)
    await db.execute(
        "UPDATE pilot_window SET started_at=now()-interval '73 hours',ends_at=now()-interval '1 second'"
    )
    work = AsyncMock(side_effect=AssertionError("Expired pilot must not run any stages"))
    monkeypatch.setattr(cli, "_cycle_task", work)
    result = await cli.cycle_task(settings, db, blobs)
    assert result["stopped"]
    work.assert_not_called()
    assert await db.rows("SELECT * FROM monitor_cycles") == []


@pytest.mark.postgres
async def test_deadline_interrupts_an_active_cycle_and_preserves_state(
    db, blobs, settings, monkeypatch
):
    settings.pilot_hours = 72
    await pilot_status(db, 72)
    await db.execute(
        "UPDATE pilot_window SET started_at=now()-interval '1 hour',ends_at=now()+interval '0.3 seconds'"
    )
    cancelled = asyncio.Event()

    async def work(settings, db, blobs, cycle_id):
        await db.execute("INSERT INTO monitor_cycles(id) VALUES(:id)", id=cycle_id)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(cli, "_cycle_task", work)
    result = await cli.cycle_task(settings, db, blobs)
    assert cancelled.is_set()
    assert result["stopped"] and result["pilot"]["expired"]
    assert (await db.rows("SELECT status FROM monitor_cycles"))[0]["status"] == "STOPPED"


@pytest.mark.postgres
async def test_unrelated_timeout_is_not_misreported_as_pilot_expiry(
    db, blobs, settings, monkeypatch
):
    settings.pilot_hours = 72
    monkeypatch.setattr(
        cli, "_cycle_task", AsyncMock(side_effect=TimeoutError("Database timed out"))
    )
    with pytest.raises(TimeoutError, match="Database timed out"):
        await cli.cycle_task(settings, db, blobs)
    assert not (await pilot_status(db))["expired"]
