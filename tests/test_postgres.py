import asyncio

import pytest

from protocol_intel.analysis import (
    Assessment,
    Finding,
    claim_job,
    process_job,
    queue_clusters,
    reconcile_jobs,
    retry_job,
)
from protocol_intel.cli import analyze_task
from protocol_intel.config import Analysis
from protocol_intel.telegram import UncertainDelivery, deliver_one

pytestmark = pytest.mark.postgres


async def observe(db, blobs, source, body):
    await db.execute("UPDATE sources SET next_check_at=now() WHERE id=:id", id=source.identity())
    state = (await db.claim(1))[0]
    content_hash = await blobs.put(body.encode())
    return await db.finish(state, content_hash, content_hash, {"url": source.url})


class FakeAnalyzer:
    def __init__(self):
        self.calls = []

    async def assess(self, payload, evidence):
        self.calls.append(payload["kind"])
        findings = []
        if payload["kind"] == "general":
            findings = [
                Finding(
                    title="Novel endpoint",
                    importance="HIGH",
                    observed_change="The API endpoint changed.",
                    significance="A new capability may be available.",
                    evidence=[payload["events"][0]],
                    uncertainty="Documentation alone does not prove deployment.",
                )
            ]
        return Assessment(findings=findings, nonmaterial_summary="No additional findings."), {
            "test": True
        }

    async def close(self):
        pass


async def material_report(db, blobs, protocol, source, settings):
    await db.sync([protocol])
    await observe(db, blobs, source, "endpoint: /v1\n")
    await observe(db, blobs, source, "endpoint: /v2\n")
    assert await queue_clusters(db, settings) == 1
    job = await claim_job(db)
    await process_job(db, blobs, FakeAnalyzer(), job, settings)
    return job


async def test_baseline_unchanged_and_repeated_transitions(db, blobs, protocol, source, settings):
    await db.sync([protocol])
    outcomes = [await observe(db, blobs, source, value) for value in ("A", "A", "B", "A", "B")]
    assert outcomes == ["BASELINE", "UNCHANGED", "CHANGED", "CHANGED", "CHANGED"]
    events = await db.rows("SELECT * FROM events ORDER BY sequence")
    assert len(events) == 4
    assert [e["baseline"] for e in events] == [True, False, False, False]
    assert events[1]["old_hash"] == events[3]["old_hash"]
    assert events[1]["new_hash"] == events[3]["new_hash"]
    assert events[1]["id"] != events[3]["id"]
    for _ in range(3):
        assert await queue_clusters(db, settings) == 1
    assert await queue_clusters(db, settings) == 0
    assert (await db.rows("SELECT count(*) AS n FROM job_events"))[0]["n"] == 3


async def test_baseline_does_not_construct_model(
    db, blobs, protocol, source, settings, monkeypatch
):
    await db.sync([protocol])
    await observe(db, blobs, source, "Existing public content")

    def forbidden(_):
        pytest.fail("Baseline must not initialize the model client")

    monkeypatch.setattr("protocol_intel.cli.OpenAIAnalyzer", forbidden)
    assert await analyze_task(settings, db, 10) == {"jobs_processed": 0, "model_calls": 0}
    assert await db.rows("SELECT * FROM outbox") == []


async def test_failure_preserves_evidence_and_recovery_resets_cadence(db, blobs, protocol, source):
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    before = (await db.rows("SELECT * FROM sources"))[0]
    await db.execute(
        "UPDATE sources SET next_check_at=now(),stable_since=now()-interval '100 days'"
    )
    claim = (await db.claim(1))[0]
    await db.fail(claim, "upstream unavailable")
    failed = (await db.rows("SELECT * FROM sources"))[0]
    assert failed["current_hash"] == before["current_hash"]
    assert failed["last_success_at"] == before["last_success_at"]
    assert failed["failures"] == 1
    assert await observe(db, blobs, source, "A") == "UNCHANGED"
    recovered = (await db.rows("SELECT * FROM sources"))[0]
    assert recovered["failures"] == 0
    assert recovered["stable_since"] > before["last_success_at"]
    assert (
        recovered["next_check_at"] - recovered["last_success_at"]
    ).total_seconds() == source.interval_seconds


async def test_expired_and_reclaimed_collectors_cannot_commit(db, blobs, protocol, source):
    await db.sync([protocol])
    old = (await db.claim(1))[0]
    old_hash = await blobs.put(b"late")
    await db.execute("UPDATE sources SET lease_until=now()-interval '1 second'")
    assert await db.finish(old, old_hash, old_hash, {"url": source.url}) == "STALE"
    await db.fail(old, "late failure")
    assert (await db.rows("SELECT failures FROM sources"))[0]["failures"] == 0
    new = (await db.claim(1))[0]
    new_hash = await blobs.put(b"current")
    assert await db.finish(new, new_hash, new_hash, {"url": source.url}) == "BASELINE"
    assert await db.finish(old, old_hash, old_hash, {"url": source.url}) == "STALE"
    assert (await db.rows("SELECT current_hash FROM sources"))[0]["current_hash"] == new_hash
    assert len(await db.rows("SELECT * FROM versions")) == 1


async def test_new_subscriber_does_not_replay_shared_history(db, blobs, protocol, source, settings):
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    await observe(db, blobs, source, "B")
    second = protocol.model_copy(update={"id": "second", "name": "Second protocol"})
    await db.sync([protocol, second])
    assert len(await db.rows("SELECT * FROM sources")) == 1
    assert await queue_clusters(db, settings) == 1
    assert [row["protocol_id"] for row in await db.rows("SELECT * FROM jobs")] == [protocol.id]
    await observe(db, blobs, source, "C")
    assert await queue_clusters(db, settings) == 2


async def test_concurrent_queue_and_claim_have_single_owner(db, blobs, protocol, source, settings):
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    await observe(db, blobs, source, "B")
    assert (
        sum(await asyncio.gather(queue_clusters(db, settings), queue_clusters(db, settings))) == 1
    )
    claimed = await asyncio.gather(claim_job(db), claim_job(db))
    assert sum(job is not None for job in claimed) == 1


async def test_focus_cannot_suppress_general_report(db, blobs, protocol, source, settings):
    protocol.analysis = Analysis(
        mode="always_deep", focus=["Only a narrowly defined old objective"]
    )
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    await observe(db, blobs, source, "B")
    await queue_clusters(db, settings)
    general = await claim_job(db)
    assert general["kind"] == "general"
    assert "focus" not in general["payload"]
    assert await claim_job(db) is None  # Focus waits for the independent general report.
    analyzer = FakeAnalyzer()
    await process_job(db, blobs, analyzer, general, settings)
    focus = await claim_job(db)
    assert focus["kind"] == "focus"
    await process_job(db, blobs, analyzer, focus, settings)
    reports = await db.rows("SELECT * FROM reports ORDER BY material DESC")
    assert [r["material"] for r in reports] == [True, False]
    outbox = await db.rows("SELECT * FROM outbox")
    assert len(outbox) == 2
    assert all(row["report_id"] == general["id"] for row in outbox)
    report_text = (await blobs.get(reports[0]["blob_hash"])).decode()
    assert "```diff" in report_text and "**Uncertainty:**" in report_text


async def test_stale_analysis_cannot_write_chunks_or_report(db, blobs, protocol, source, settings):
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    await observe(db, blobs, source, "B")
    await queue_clusters(db, settings)
    job = await claim_job(db)

    class StaleAnalyzer(FakeAnalyzer):
        async def assess(self, payload, evidence):
            await db.execute("UPDATE jobs SET lease_until=now()-interval '1 second'")
            return await super().assess(payload, evidence)

    with pytest.raises(ValueError, match="lease expired"):
        await process_job(db, blobs, StaleAnalyzer(), job, settings)
    assert await db.rows("SELECT * FROM analysis_chunks") == []
    assert await db.rows("SELECT * FROM reports") == []


async def test_crashed_analysis_is_reclaimed_without_other_pending_jobs(
    db, blobs, protocol, source, settings, monkeypatch
):
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    await observe(db, blobs, source, "B")
    await queue_clusters(db, settings)
    await claim_job(db)
    await db.execute("UPDATE jobs SET lease_until=now()-interval '1 second'")
    monkeypatch.setattr("protocol_intel.cli.OpenAIAnalyzer", lambda _: FakeAnalyzer())
    result = await analyze_task(settings, db, 10)
    assert result["jobs_processed"] == 1
    assert (await db.rows("SELECT status,attempts FROM jobs"))[0] == {
        "status": "DONE",
        "attempts": 2,
    }


async def test_invented_evidence_is_failed_not_immaterial(db, blobs, protocol, source, settings):
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    await observe(db, blobs, source, "B")
    await queue_clusters(db, settings)
    job = await claim_job(db)

    class InvalidAnalyzer(FakeAnalyzer):
        async def assess(self, payload, evidence):
            result, usage = await super().assess(payload, evidence)
            result.findings[0].evidence = ["invented-id"]
            return result, usage

    with pytest.raises(ValueError, match="invented"):
        await process_job(db, blobs, InvalidAnalyzer(), job, settings)
    assert await db.rows("SELECT * FROM reports") == []
    assert (await db.rows("SELECT status,last_error FROM jobs"))[0]["last_error"]


async def test_uncertain_telegram_send_is_never_blindly_retried(
    db, blobs, protocol, source, settings
):
    await material_report(db, blobs, protocol, source, settings)

    class AmbiguousTelegram:
        destination = settings.telegram_chat_id
        calls = 0

        async def check(self, destination):
            return {"channel_id": destination}

        async def call(self, *args, **kwargs):
            self.calls += 1
            raise UncertainDelivery("read timeout")

    telegram = AmbiguousTelegram()
    assert await deliver_one(db, blobs, telegram)
    assert not await deliver_one(db, blobs, telegram)
    assert telegram.calls == 1
    states = {r["part"]: r["status"] for r in await db.rows("SELECT * FROM outbox")}
    assert states == {"summary": "UNKNOWN", "document": "PENDING"}


async def test_telegram_report_parts_are_sent_once_in_order(db, blobs, protocol, source, settings):
    await material_report(db, blobs, protocol, source, settings)

    class SuccessfulTelegram:
        destination = settings.telegram_chat_id
        calls = []

        async def check(self, destination):
            return {"channel_id": destination}

        async def call(self, method, *args, **kwargs):
            self.calls.append(method)
            return {"message_id": len(self.calls)}

    telegram = SuccessfulTelegram()
    assert await deliver_one(db, blobs, telegram)
    assert await deliver_one(db, blobs, telegram)
    assert not await deliver_one(db, blobs, telegram)
    assert telegram.calls == ["sendMessage", "sendDocument"]
    assert all(r["status"] == "SENT" for r in await db.rows("SELECT * FROM outbox"))


async def test_manual_protocol_requires_explicit_selection_and_disabled_jobs_wait(
    db, blobs, protocol, source, settings
):
    protocol.analysis = Analysis(mode="manual_only")
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    await observe(db, blobs, source, "B")
    assert await queue_clusters(db, settings) == 0
    assert await queue_clusters(db, settings, protocol.id, include_manual=True) == 1
    protocol.enabled = False
    await db.sync([protocol])
    assert await claim_job(db) is None
    protocol.enabled = True
    await db.sync([protocol])
    assert (await claim_job(db, protocol.id))["protocol_id"] == protocol.id


async def test_failed_parent_blocks_focus_and_explicit_retry_recovers_it(
    db, blobs, protocol, source, settings
):
    protocol.analysis = Analysis(mode="always_deep", focus=["Optional question"])
    await db.sync([protocol])
    await observe(db, blobs, source, "A")
    await observe(db, blobs, source, "B")
    await queue_clusters(db, settings)
    general = await claim_job(db)
    await db.execute(
        "UPDATE jobs SET attempts=3,lease_until=now()-interval '1 second' WHERE id=:id",
        id=general["id"],
    )
    await reconcile_jobs(db)
    states = {r["kind"]: r for r in await db.rows("SELECT * FROM jobs")}
    assert states["general"]["status"] == "FAILED" and states["general"]["last_error"]
    assert states["focus"]["status"] == "BLOCKED" and states["focus"]["last_error"]
    original_payload = states["general"]["payload"]
    await retry_job(db, general["id"])
    retried = await claim_job(db)
    assert retried["payload"] == original_payload
    await process_job(db, blobs, FakeAnalyzer(), retried, settings)
    assert (await claim_job(db))["kind"] == "focus"
    with pytest.raises(ValueError, match="FAILED"):
        await retry_job(db, general["id"])


async def test_analysis_archives_complete_request_contract(db, blobs, protocol, source, settings):
    import json

    job = await material_report(db, blobs, protocol, source, settings)
    row = (await db.rows("SELECT * FROM analysis_chunks WHERE job_id=:id", id=job["id"]))[0]
    request = json.loads(await blobs.get(row["input_hash"]))
    assert request["input"][0]["content"] == job["payload"]["system_prompt"]
    assert request["response_schema"] == Assessment.model_json_schema()
    assert request["model"] == settings.openai_deep_model
    assert request["max_output_tokens"] == settings.deep_max_output_tokens


async def test_blob_read_failure_before_telegram_submission_is_retryable(
    db, blobs, protocol, source, settings
):
    job = await material_report(db, blobs, protocol, source, settings)
    await db.execute("UPDATE outbox SET status='SENT',message_id=1 WHERE part='summary'")

    class UnavailableBlobs:
        async def get(self, key):
            raise OSError("object storage temporarily unavailable")

    class NoSendTelegram:
        destination = settings.telegram_chat_id

        async def check(self, destination):
            return {"channel_id": destination}

        async def call(self, *args, **kwargs):
            pytest.fail("No Telegram submission may occur without the report body")

    with pytest.raises(OSError):
        await deliver_one(db, UnavailableBlobs(), NoSendTelegram())
    document = (
        await db.rows("SELECT * FROM outbox WHERE report_id=:id AND part='document'", id=job["id"])
    )[0]
    assert document["status"] == "PENDING"
    assert "before submission" in document["last_error"]
