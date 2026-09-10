import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from protocol_intel.analysis import claim_job, queue_clusters
from protocol_intel.blobs import S3Blobs, blob_key
from protocol_intel.config import canonical
from protocol_intel.costs import archive_inventory, model_cost, resource_usage
from protocol_intel.preview import generate_preview, inspect_preview, prepare_preview
from protocol_intel.telegram import deliver_one, summary_text


@pytest.fixture(autouse=True)
def price_date(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 9)

    monkeypatch.setattr("protocol_intel.costs.date", FixedDate)


def test_cost_accounts_for_cache_without_double_counting_reasoning():
    cost = model_cost(
        {
            "returned_model": "gpt-5.6-sol",
            "input_tokens": 10000,
            "output_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 5000},
            "output_tokens_details": {"reasoning_tokens": 800},
        }
    )
    assert cost["usd_low"] == pytest.approx(0.042)
    assert cost["usd_high"] == pytest.approx(0.047)
    assert not model_cost({"returned_model": "gpt-5.6-sol"})["available"]
    assert not model_cost({"returned_model": "unknown", "input_tokens": 1, "output_tokens": 1})[
        "available"
    ]
    with pytest.raises(ValueError, match="inconsistent"):
        model_cost(
            {
                "returned_model": "gpt-5.6-sol",
                "input_tokens": 1,
                "output_tokens": 1,
                "input_tokens_details": {"cached_tokens": 2},
            }
        )


def test_reported_cache_writes_produce_specific_token_cost():
    cost = model_cost(
        {
            "returned_model": "gpt-5.6-sol",
            "input_tokens": 7852,
            "output_tokens": 4469,
            "input_tokens_details": {"cache_write_tokens": 7849, "cached_tokens": 0},
        }
    )
    assert cost["usd_low"] == cost["usd_high"] == pytest.approx(0.128637)


async def test_inventory_paginates_both_owned_prefixes():
    blobs = object.__new__(S3Blobs)
    blobs.bucket = "test"
    paginator = Mock()
    paginator.paginate.side_effect = [
        [
            {"Contents": [{"Key": "sha256/a/one", "Size": 5}]},
            {"Contents": [{"Key": "sha256/b/two", "Size": 8}]},
        ],
        [{"Contents": [{"Key": "diagnostics/probe", "Size": 2}]}],
    ]
    blobs.client = Mock()
    blobs.client.get_paginator.return_value = paginator
    assert sum((await archive_inventory(blobs)).values()) == 15
    assert [c.kwargs["Prefix"] for c in paginator.paginate.call_args_list] == [
        "sha256/",
        "diagnostics/",
    ]


async def test_preview_bounds_full_serialized_request_and_omits_focus(blobs, settings):
    key = await blobs.put(("😃" * 10000).encode())
    db = SimpleNamespace(
        rows=AsyncMock(
            side_effect=[
                [{"id": "test", "name": "Test", "profile": "General profile"}],
                [
                    {
                        "id": str(i),
                        "url": f"https://example.org/{i}",
                        "current_hash": key,
                        "observed_at": "2026-09-09",
                    }
                    for i in range(20)
                ],
            ]
        )
    )
    result = await prepare_preview(db, blobs, settings, "test")
    assert len(canonical(result["request"]).encode()) <= 48000
    assert result["sample"]["sampled_pages"] == 8
    assert result["sample"]["available_archived_pages"] == 20
    assert all(s["truncated"] for s in result["sample"]["snapshots"])
    assert "focus" not in result["sample"]
    assert result["request"]["max_output_tokens"] == settings.screen_max_output_tokens
    evidence_schema = result["request"]["text"]["format"]["schema"]["$defs"]["Finding"][
        "properties"
    ]["evidence"]
    assert evidence_schema["items"]["enum"] == [s["id"] for s in result["sample"]["snapshots"]]
    assert evidence_schema["minItems"] == 1


async def baseline(db, blobs, protocol):
    await db.sync([protocol])
    state = (await db.claim(1))[0]
    key = await blobs.put(b"Existing protocol documentation")
    assert await db.finish(state, key, key, {"url": state["spec"]["url"]}) == "BASELINE"
    return key


def analyzer_response(status="completed", content=None):
    result = {"findings": [], "nonmaterial_summary": "No consequential properties in this sample."}
    raw = {
        "id": "resp_test",
        "model": "gpt-5.6-sol",
        "status": status,
        "usage": {"input_tokens": 2000, "output_tokens": 500},
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": content if content is not None else canonical(result),
                    }
                ],
            }
        ],
    }
    create = AsyncMock(return_value=SimpleNamespace(model_dump_json=lambda: canonical(raw)))
    return SimpleNamespace(client=SimpleNamespace(responses=SimpleNamespace(create=create)))


@pytest.mark.postgres
async def test_paid_preview_is_cached_and_does_not_create_real_change_work(
    db, blobs, protocol, settings
):
    await baseline(db, blobs, protocol)
    analyzer = analyzer_response()
    report = await generate_preview(db, blobs, analyzer, settings, protocol.id, "cached")
    again = await generate_preview(db, blobs, analyzer, settings, protocol.id, "cached")
    assert report["id"] == again["id"]
    assert analyzer.client.responses.create.await_count == 1
    assert not report["material"]
    assert "TEST" in (await blobs.get(report["blob_hash"])).decode()
    assert report["result"]["_preview"]["usage"]["input_tokens"] == 2000
    assert await queue_clusters(db, settings) == 0
    assert await claim_job(db) is None
    assert await db.rows("SELECT * FROM outbox") == []
    assert all(e["baseline"] for e in await db.rows("SELECT * FROM events"))


@pytest.mark.postgres
@pytest.mark.parametrize("status,content", [("incomplete", None), ("completed", "invalid JSON")])
async def test_failed_output_retains_usage_and_never_repeats_paid_call(
    db, blobs, protocol, settings, status, content
):
    await baseline(db, blobs, protocol)
    analyzer = analyzer_response(status, content)
    for _ in range(2):
        with pytest.raises(ValueError):
            await generate_preview(db, blobs, analyzer, settings, protocol.id, "invalid")
    assert analyzer.client.responses.create.await_count == 1
    receipt = (await db.rows("SELECT * FROM analysis_chunks"))[0]
    assert receipt["usage"]["output_tokens"] == 500
    assert json.loads(await blobs.get(receipt["result"]["raw_response_hash"]))["status"] == status
    assert await db.rows("SELECT * FROM reports") == []


@pytest.mark.postgres
async def test_uncertain_paid_call_is_not_automatically_retried(db, blobs, protocol, settings):
    await baseline(db, blobs, protocol)
    analyzer = analyzer_response()
    analyzer.client.responses.create.side_effect = TimeoutError("unknown outcome")
    with pytest.raises(TimeoutError):
        await generate_preview(db, blobs, analyzer, settings, protocol.id, "uncertain")
    with pytest.raises(ValueError, match="uncertain"):
        await generate_preview(db, blobs, analyzer, settings, protocol.id, "uncertain")
    assert analyzer.client.responses.create.await_count == 1


@pytest.mark.postgres
async def test_concurrent_preview_claim_submits_only_once(db, blobs, protocol, settings):
    await baseline(db, blobs, protocol)
    analyzer = analyzer_response()
    responses = await asyncio.gather(
        *(
            generate_preview(db, blobs, analyzer, settings, protocol.id, "concurrent")
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert any(isinstance(r, dict) for r in responses)
    assert all(isinstance(r, (dict, ValueError)) for r in responses)
    assert analyzer.client.responses.create.await_count == 1


@pytest.mark.postgres
async def test_storage_counts_shared_content_once_and_detects_missing_evidence(db, blobs, protocol):
    key = await baseline(db, blobs, protocol)
    second = protocol.model_copy(update={"id": "second", "name": "Second"})
    await db.sync([protocol, second])
    metrics = await resource_usage(db, blobs)
    size = len(await blobs.get(key))
    assert metrics["archive_bytes"] == size
    assert metrics["database_physical_bytes"] > 0
    assert len(metrics["protocols"]) == 2
    for row in metrics["protocols"]:
        assert row["archive_referenced_bytes"] == row["archive_shared_bytes"] == size
        assert row["archive_exclusive_bytes"] == 0
    (blobs.root / blob_key(key)).unlink()
    with pytest.raises(ValueError, match="missing"):
        await resource_usage(db, blobs)


@pytest.mark.postgres
async def test_test_delivery_is_scoped_labeled_and_deduplicated(db, blobs, protocol, settings):
    await baseline(db, blobs, protocol)
    analyzer = analyzer_response()
    reports = [
        await generate_preview(db, blobs, analyzer, settings, protocol.id, name)
        for name in ("older", "selected")
    ]
    for report in reports:
        for part in ("summary", "document"):
            await db.execute(
                "INSERT INTO outbox(id,report_id,destination,part) VALUES(:id,:report,:destination,:part)",
                id=report["id"] + part,
                report=report["id"],
                destination=settings.telegram_chat_id,
                part=part,
            )
    telegram = SimpleNamespace(
        destination=settings.telegram_chat_id,
        check=AsyncMock(return_value={"channel_id": settings.telegram_chat_id}),
        call=AsyncMock(return_value={"message_id": 123}),
    )
    for _ in range(2):
        assert await deliver_one(db, blobs, telegram, report_id=reports[1]["id"])
    assert not await deliver_one(db, blobs, telegram, report_id=reports[1]["id"])
    assert telegram.call.await_count == 2
    summary, document = telegram.call.call_args_list
    assert "TEST — BASELINE REVIEW" in summary.args[1]["text"]
    assert "TEST baseline review" in document.args[1]["caption"]
    assert "not a detected change" in summary_text({**reports[1], "report_id": reports[1]["id"]})
    assert all(
        r["status"] == "PENDING"
        for r in await db.rows("SELECT status FROM outbox WHERE report_id=:id", id=reports[0]["id"])
    )


@pytest.mark.postgres
async def test_inspection_exposes_invalid_references_without_approving_or_recharging(
    db, blobs, protocol, settings
):
    await baseline(db, blobs, protocol)
    content = canonical(
        {
            "findings": [
                {
                    "title": "Unverified",
                    "importance": "HIGH",
                    "observed_change": "Existing state",
                    "significance": "Possible implication",
                    "evidence": ["invented-reference"],
                    "uncertainty": "Unknown",
                }
            ],
            "nonmaterial_summary": "",
        }
    )
    analyzer = analyzer_response(content=content)
    with pytest.raises(ValueError, match="missing snapshot evidence"):
        await generate_preview(db, blobs, analyzer, settings, protocol.id, "inspect")
    document = await inspect_preview(db, blobs, "inspect")
    assert "invented-reference" in document
    assert "snapshot-1" in document
    assert protocol.sources[0].identity() in document
    assert "Measured storage" in document
    assert "unverified" in document
    assert analyzer.client.responses.create.await_count == 1
    assert await db.rows("SELECT * FROM reports") == []
    assert await db.rows("SELECT * FROM outbox") == []
    assert (await db.rows("SELECT status FROM jobs"))[0]["status"] == "TEST_RESPONDED"


@pytest.mark.postgres
async def test_inspecting_missing_receipt_never_creates_work(db, blobs):
    with pytest.raises(ValueError, match="no model call"):
        await inspect_preview(db, blobs, "not-found")
    assert await db.rows("SELECT * FROM jobs") == []
