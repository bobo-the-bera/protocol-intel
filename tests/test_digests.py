import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from protocol_intel.config import canonical
from protocol_intel.digests import queue_daily_digest, window
from protocol_intel.telegram import DeliveryError, UncertainDelivery, deliver_one, summary_text

DAY = date(2026, 9, 9)
START, END = window(DAY)
NOW = END + timedelta(hours=8)


async def report(
    db, blobs, protocol, name, at, *, kind="general", importance="MEDIUM", alerted=False
):
    await db.execute(
        "INSERT INTO jobs(id,protocol_id,kind,payload,status) VALUES(:id,:protocol,:kind,'{\"analysis_mode\":\"tiered\"}','DONE')",
        id=name,
        protocol=protocol.id,
        kind=kind,
    )
    finding = {
        "title": "Integration changed",
        "importance": importance,
        "observed_change": "An API field changed",
        "significance": "New integration path",
        "evidence": ["event-1"],
        "uncertainty": "Deployment not verified",
    }
    result = {"findings": [finding], "nonmaterial_summary": "Stored analysis notes"}
    if kind == "baseline_test":
        result["_preview"] = {}
    key = await blobs.put(b"Full original evidence report")
    await db.execute(
        "INSERT INTO reports(id,job_id,protocol_id,material,result,blob_hash,created_at) VALUES(:id,:id,:protocol,true,CAST(:result AS jsonb),:hash,:at)",
        id=name,
        protocol=protocol.id,
        result=canonical(result),
        hash=key,
        at=at,
    )
    if alerted:
        await db.execute(
            "INSERT INTO outbox(id,report_id,destination,part,status) VALUES(:id,:report,'channel','summary','SENT')",
            id=name + "-alert",
            report=name,
        )
    return key


def test_utc_windows_are_half_open_across_year_and_leap_day():
    assert window(date(2024, 2, 29))[1] == datetime(2024, 3, 1, tzinfo=UTC)
    assert window(date(2026, 12, 31))[1] == datetime(2027, 1, 1, tzinfo=UTC)


@pytest.mark.postgres
async def test_digest_boundaries_baseline_exclusion_and_focus_dedup(db, blobs, protocol, settings):
    await db.sync([protocol])
    await report(db, blobs, protocol, "before", START - timedelta(microseconds=1))
    await report(db, blobs, protocol, "start", START, importance="HIGH", alerted=True)
    await report(db, blobs, protocol, "focus", END - timedelta(microseconds=1), kind="focus")
    await report(db, blobs, protocol, "end", END)
    await report(db, blobs, protocol, "preview", START, kind="baseline_test")
    result = await queue_daily_digest(db, blobs, settings, NOW)
    assert result == "daily-digest-2026-09-09"
    stored = (await db.rows("SELECT * FROM reports WHERE id=:id", id=result))[0]
    body = (await blobs.get(stored["blob_hash"])).decode()
    assert "1 notable findings; 2 completed analyses" in body
    assert "Reports: start, focus" in body
    assert '"id":"before"' not in body
    assert '"id":"end"' not in body
    assert '"id":"preview"' not in body
    summary = summary_text({**stored, "report_id": result})
    assert "already alerted" in summary
    assert "no AI call" in summary
    assert len(summary.encode("utf-16-le")) < 8192
    assert len(await db.rows("SELECT * FROM outbox WHERE report_id=:id", id=result)) == 2
    assert await queue_daily_digest(db, blobs, settings, NOW) is None


@pytest.mark.postgres
async def test_clock_gate_and_late_analysis_appear_on_next_day(db, blobs, protocol, settings):
    await db.sync([protocol])
    assert await queue_daily_digest(db, blobs, settings, NOW - timedelta(seconds=1)) is None
    assert await db.rows("SELECT * FROM reports") == []
    await queue_daily_digest(db, blobs, settings, NOW)
    await report(db, blobs, protocol, "late", END + timedelta(hours=3))
    next_id = await queue_daily_digest(db, blobs, settings, NOW + timedelta(days=1))
    stored = (await db.rows("SELECT * FROM reports WHERE id=:id", id=next_id))[0]
    assert "Reports: late" in (await blobs.get(stored["blob_hash"])).decode()


@pytest.mark.postgres
async def test_outage_catchup_is_chronological_and_persistent(db, blobs, settings):
    await queue_daily_digest(db, blobs, settings, NOW - timedelta(seconds=1))
    for offset in range(4):
        report_id = await queue_daily_digest(db, blobs, settings, NOW + timedelta(days=3))
        assert report_id == "daily-digest-" + (DAY + timedelta(days=offset)).isoformat()
    assert await queue_daily_digest(db, blobs, settings, NOW + timedelta(days=3)) is None
    assert len(await db.rows("SELECT * FROM reports")) == 4


@pytest.mark.postgres
async def test_upload_failure_does_not_advance_cursor_or_queue_partial_delivery(
    db, blobs, settings
):
    failing = SimpleNamespace(
        put=AsyncMock(side_effect=OSError("storage unavailable")), get=blobs.get
    )
    with pytest.raises(OSError):
        await queue_daily_digest(db, failing, settings, NOW)
    assert await db.rows("SELECT * FROM reports") == []
    assert await db.rows("SELECT * FROM outbox") == []
    assert (await db.rows("SELECT next_day FROM daily_digest_state"))[0]["next_day"] == DAY
    assert (
        await queue_daily_digest(db, blobs, settings, NOW + timedelta(days=1))
        == "daily-digest-2026-09-09"
    )


@pytest.mark.postgres
async def test_missing_original_report_blob_blocks_digest(db, blobs, protocol, settings):
    from protocol_intel.blobs import blob_key

    await db.sync([protocol])
    key = await report(db, blobs, protocol, "missing", START)
    (blobs.root / blob_key(key)).unlink()
    with pytest.raises(FileNotFoundError):
        await queue_daily_digest(db, blobs, settings, NOW)
    assert await db.rows("SELECT * FROM outbox") == []


@pytest.mark.postgres
async def test_concurrent_digest_workers_queue_exactly_one_report(db, blobs, settings):
    results = await asyncio.gather(
        *(queue_daily_digest(db, blobs, settings, NOW) for _ in range(3))
    )
    assert sum(r is not None for r in results) == 1
    assert len(await db.rows("SELECT * FROM reports")) == 1
    assert len(await db.rows("SELECT * FROM outbox")) == 2


@pytest.mark.postgres
async def test_health_failures_pending_events_and_failed_calls_are_visible(
    db, blobs, protocol, settings
):
    await db.sync([protocol])
    source = protocol.sources[0].identity()
    await db.execute(
        "UPDATE sources SET failures=2,last_error='Fetch failed',next_check_at=:at", at=START
    )
    await db.execute(
        "INSERT INTO events(id,source_id,sequence,kind,baseline,new_hash,metadata,created_at) VALUES('pending',:source,1,'PAGE_MODIFIED',false,'hash','{}',:at)",
        source=source,
        at=START,
    )
    await db.execute(
        "INSERT INTO checks(id,source_id,outcome,checked_at) VALUES('check',:source,'FAILED_TO_CHECK',:at)",
        source=source,
        at=START,
    )
    await db.execute(
        "INSERT INTO jobs(id,protocol_id,kind,payload,status,last_error) VALUES('failed',:protocol,'general','{\"analysis_mode\":\"tiered\"}','FAILED','Invalid model response')",
        protocol=protocol.id,
    )
    await db.execute(
        "INSERT INTO model_calls(job_id,call_key,input_hash,status,completed_at,usage) VALUES('failed','screen','hash','RESPONDED',:at,CAST(:usage AS jsonb))",
        at=START,
        usage=canonical(
            {"returned_model": "gpt-5.6-luna", "input_tokens": 100, "output_tokens": 20}
        ),
    )
    await db.execute(
        "INSERT INTO model_calls(job_id,call_key,input_hash,status) VALUES('failed','deep','hash','CALLING')"
    )
    result = await queue_daily_digest(db, blobs, settings, NOW)
    stored = (await db.rows("SELECT * FROM reports WHERE id=:id", id=result))[0]
    body = (await blobs.get(stored["blob_hash"])).decode()
    assert "0 notable findings" in body
    assert "2 unfinished jobs/unassigned events" in body
    assert "Failed checks during day: 1" in body
    assert "Invalid model response" in body
    assert "Fetch failed" in body
    assert "gpt-5.6-luna" in body
    assert "Unresolved API submissions: 1" in body
    assert "does not establish no changes" in body


@pytest.mark.postgres
async def test_digest_delivery_retries_and_deduplicates_summary_and_document(db, blobs, settings):
    report_id = await queue_daily_digest(db, blobs, settings, NOW)
    telegram = SimpleNamespace(
        destination=settings.telegram_chat_id,
        check=AsyncMock(return_value={"channel_id": settings.telegram_chat_id}),
        call=AsyncMock(
            side_effect=[
                DeliveryError("Rate limited", retry_after=0),
                {"message_id": 1},
                {"message_id": 2},
            ]
        ),
    )
    for _ in range(3):
        assert await deliver_one(db, blobs, telegram, report_id)
    assert not await deliver_one(db, blobs, telegram, report_id)
    assert "DAILY DIGEST" in telegram.call.call_args_list[1].args[1]["text"]
    assert "Daily digest — 2026-09-09 UTC" == telegram.call.call_args_list[2].args[1]["caption"]
    assert all(row["status"] == "SENT" for row in await db.rows("SELECT status FROM outbox"))
    assert await queue_daily_digest(db, blobs, settings, NOW) is None


@pytest.mark.postgres
async def test_ambiguous_digest_send_stops_automatic_retry(db, blobs, settings):
    report_id = await queue_daily_digest(db, blobs, settings, NOW)
    telegram = SimpleNamespace(
        destination=settings.telegram_chat_id,
        check=AsyncMock(return_value={"channel_id": settings.telegram_chat_id}),
        call=AsyncMock(side_effect=UncertainDelivery()),
    )
    await deliver_one(db, blobs, telegram, report_id)
    assert not await deliver_one(db, blobs, telegram, report_id)
    assert telegram.call.await_count == 1
    assert (await db.rows("SELECT status FROM outbox WHERE part='summary'"))[0][
        "status"
    ] == "UNKNOWN"


@pytest.mark.postgres
async def test_cycle_continues_to_digest_and_delivery_after_collection_failure(
    db, blobs, settings, monkeypatch
):
    from protocol_intel import cli

    settings.collection_enabled = settings.analysis_enabled = settings.daily_digest_enabled = (
        settings.notifications_enabled
    ) = True
    monkeypatch.setattr(cli, "HTTP", lambda *_: SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(cli, "run_due", AsyncMock(side_effect=OSError("Collector failed")))
    monkeypatch.setattr(
        cli, "analyze_task", AsyncMock(return_value={"jobs_processed": 0, "model_calls": 0})
    )
    digest_call = AsyncMock(return_value="daily-test")
    monkeypatch.setattr(cli, "queue_daily_digest", digest_call)
    deliver = AsyncMock(return_value=False)
    monkeypatch.setattr(cli, "deliver_one", deliver)
    monkeypatch.setattr(cli, "Telegram", lambda *_: SimpleNamespace(close=AsyncMock()))
    result = await cli.cycle_task(settings, db, blobs)
    assert "collection" in result["errors"]
    digest_call.assert_awaited_once()
    deliver.assert_awaited_once()
    assert (await db.rows("SELECT status FROM monitor_cycles"))[0]["status"] == "FAILED"


@pytest.mark.postgres
async def test_no_changes_cycle_constructs_no_ai_client(db, blobs, settings, protocol, monkeypatch):
    from protocol_intel import cli

    await db.sync([protocol])
    settings.collection_enabled = settings.analysis_enabled = True
    monkeypatch.setattr(cli, "HTTP", lambda *_: SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(cli, "run_due", AsyncMock(return_value={"UNCHANGED": 1}))
    client = Mock(side_effect=AssertionError("No changed events should use AI"))
    monkeypatch.setattr(cli, "OpenAIAnalyzer", client)
    result = await cli.cycle_task(settings, db, blobs)
    assert not result["errors"]
    assert result["analysis"]["model_calls"] == 0
    client.assert_not_called()
