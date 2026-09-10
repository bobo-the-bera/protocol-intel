import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from protocol_intel.analysis import claim_job, process_job, queue_clusters
from protocol_intel.config import Analysis, canonical
from protocol_intel.tiered import bounded_chunks, parse_response, request_contract


def payload():
    return {
        "protocol": "Test",
        "profile": "Neutral",
        "coverage": [],
        "prior": [],
        "kind": "general",
        "screen_model": "gpt-5.6-luna",
        "screen_effort": "low",
        "model": "gpt-5.6-sol",
        "effort": "medium",
        "system_prompt": "Broad analysis",
        "screen_max_output_tokens": 2000,
        "max_output_tokens": 6000,
        "request_max_bytes": 16000,
        "max_chunks": 64,
    }


def test_request_budget_preserves_every_character_and_excludes_focus():
    evidence = {
        "events": [
            {"id": "full-original-id", "url": "https://example.org", "diff": "+" + "🧪" * 12000}
        ]
    }
    value = {**payload(), "focus": ["a narrow old objective"]}
    chunks = bounded_chunks(value, [evidence])
    assert len(chunks) > 1
    assert "".join(e["diff"] for c in chunks for e in c["events"]) == evidence["events"][0]["diff"]
    for chunk in chunks:
        for screen in (True, False):
            contract = request_contract(value, chunk, screen)
            assert len(canonical(contract["request"]).encode()) <= 16000
            assert "a narrow old objective" not in canonical(contract["request"])
            assert contract["aliases"] == {"e1": "full-original-id"}


def test_unbounded_metadata_fails_before_spending():
    with pytest.raises(ValueError, match="metadata exceeds"):
        bounded_chunks(
            {**payload(), "profile": "x" * 20000}, [{"events": [{"id": "one", "diff": "+x"}]}]
        )


class FakeAPI:
    def __init__(
        self,
        disposition="routine",
        confidence="high",
        screen_status="completed",
        deep_status="completed",
        deep_importance="HIGH",
        cross_novel=False,
    ):
        self.models = []
        self.disposition = disposition
        self.confidence = confidence
        self.screen_status = screen_status
        self.deep_status = deep_status
        self.deep_importance = deep_importance
        self.cross_novel = cross_novel
        self.client = SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(side_effect=self.create))
        )

    async def create(self, **request):
        self.models.append(request["model"])
        data = json.loads(request["input"][1]["content"])
        ids = sorted({e["id"] for e in data.get("events", [])} | set(data.get("event_ids", [])))
        screen = request["text"]["format"]["name"] == "screening"
        assessment = {"findings": [], "nonmaterial_summary": "Reviewed the supplied changes."}
        if screen:
            result = {
                "disposition": "novel"
                if self.cross_novel and "screenings" in data
                else self.disposition,
                "confidence": self.confidence,
                "reason": "Reviewed broadly",
                "reviewed_event_ids": ids,
                "assessment": assessment,
            }
        else:
            assessment["findings"] = [
                {
                    "title": "Parameter change",
                    "importance": self.deep_importance,
                    "observed_change": "One configuration value changed",
                    "significance": "Potential new behavior",
                    "uncertainty": "Deployment not proven",
                    "evidence": ids,
                }
            ]
            result = assessment
        raw = {
            "id": f"response-{len(self.models)}",
            "model": request["model"],
            "status": self.screen_status if screen else self.deep_status,
            "usage": {"input_tokens": 1000, "output_tokens": 200},
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": canonical(result)}]}
            ],
        }
        return SimpleNamespace(model_dump_json=lambda: canonical(raw))


async def job_with_change(db, blobs, protocol, settings, after="flag: true\n"):
    protocol = protocol.model_copy(update={"analysis": Analysis(mode="tiered")})
    settings.analysis_audit_percent = 0
    await db.sync([protocol])
    for content in ("flag: false\n", after):
        await db.execute("UPDATE sources SET next_check_at=now()")
        state = (await db.claim(1))[0]
        key = await blobs.put(content.encode())
        await db.finish(state, key, key, {"url": state["spec"]["url"]})
    assert await queue_clusters(db, settings) == 1
    return await claim_job(db)


@pytest.mark.postgres
@pytest.mark.parametrize(
    "disposition,confidence,expected",
    [
        ("routine", "high", 1),
        ("routine", "medium", 2),
        ("important", "high", 2),
        ("novel", "high", 2),
        ("uncertain", "high", 2),
    ],
)
async def test_routing_and_alerts(db, blobs, protocol, settings, disposition, confidence, expected):
    job = await job_with_change(db, blobs, protocol, settings)
    api = FakeAPI(disposition, confidence)
    await process_job(db, blobs, api, job, settings)
    assert api.models == ["gpt-5.6-luna"] + (["gpt-5.6-sol"] if expected == 2 else [])
    report = (await db.rows("SELECT * FROM reports"))[0]
    assert len(report["result"]["_pipeline"]["model_calls"]) == expected
    assert len(await db.rows("SELECT * FROM outbox")) == (2 if expected == 2 else 0)
    if expected == 2:
        assert report["result"]["findings"][0]["evidence"] == job["payload"]["events"]
    assert (await db.rows("SELECT status FROM jobs WHERE id=:id", id=job["id"]))[0][
        "status"
    ] == "DONE"


@pytest.mark.postgres
async def test_routine_audit_can_find_missed_signal(db, blobs, protocol, settings):
    job = await job_with_change(db, blobs, protocol, settings)
    job["payload"]["audit_percent"] = 100
    api = FakeAPI()
    await process_job(db, blobs, api, job, settings)
    assert api.models == ["gpt-5.6-luna", "gpt-5.6-sol"]
    report = (await db.rows("SELECT result FROM reports"))[0]["result"]
    assert "audit" in report["_pipeline"]["deep_reason"]
    assert len(await db.rows("SELECT * FROM outbox")) == 2


@pytest.mark.postgres
async def test_incomplete_screen_escalates_but_incomplete_deep_is_retained(
    db, blobs, protocol, settings
):
    job = await job_with_change(db, blobs, protocol, settings)
    api = FakeAPI(screen_status="incomplete", deep_status="incomplete")
    with pytest.raises(ValueError, match="Incomplete"):
        await process_job(db, blobs, api, job, settings)
    await db.execute("UPDATE jobs SET next_attempt_at=now()")
    with pytest.raises(ValueError, match="Incomplete"):
        await process_job(db, blobs, api, await claim_job(db), settings)
    assert api.models == ["gpt-5.6-luna", "gpt-5.6-sol"]
    receipts = await db.rows("SELECT status,usage FROM model_calls")
    assert len(receipts) == 2
    assert all(r["status"] == "RESPONDED" and r["usage"]["output_tokens"] == 200 for r in receipts)
    assert await db.rows("SELECT * FROM reports") == []


@pytest.mark.postgres
async def test_unknown_submission_is_not_repaid_or_blindly_escalated(db, blobs, protocol, settings):
    job = await job_with_change(db, blobs, protocol, settings)
    api = FakeAPI()
    api.client.responses.create.side_effect = TimeoutError("unknown outcome")
    with pytest.raises(TimeoutError):
        await process_job(db, blobs, api, job, settings)
    await db.execute("UPDATE jobs SET next_attempt_at=now()")
    with pytest.raises(ValueError, match="uncertain"):
        await process_job(db, blobs, api, await claim_job(db), settings)
    assert api.client.responses.create.await_count == 1
    assert await db.rows("SELECT * FROM reports") == []


@pytest.mark.postgres
async def test_report_storage_failure_reuses_both_paid_responses(
    db, blobs, protocol, settings, monkeypatch
):
    job = await job_with_change(db, blobs, protocol, settings)
    api = FakeAPI(disposition="novel")
    put = blobs.put
    failed = False

    async def fail_report_once(data):
        nonlocal failed
        if data.startswith(b"# Test protocol") and not failed:
            failed = True
            raise OSError("temporary archive failure")
        return await put(data)

    monkeypatch.setattr(blobs, "put", fail_report_once)
    with pytest.raises(OSError):
        await process_job(db, blobs, api, job, settings)
    await db.execute("UPDATE jobs SET next_attempt_at=now()")
    await process_job(db, blobs, api, await claim_job(db), settings)
    assert failed
    assert api.client.responses.create.await_count == 2
    assert len(await db.rows("SELECT * FROM reports")) == 1


@pytest.mark.postgres
async def test_medium_is_stored_but_not_immediately_notified(db, blobs, protocol, settings):
    job = await job_with_change(db, blobs, protocol, settings)
    await process_job(
        db, blobs, FakeAPI(disposition="important", deep_importance="MEDIUM"), job, settings
    )
    assert (await db.rows("SELECT material FROM reports"))[0]["material"]
    assert await db.rows("SELECT * FROM outbox") == []


@pytest.mark.postgres
async def test_cross_chunk_connections_escalate_after_routine_individual_screens(
    db, blobs, protocol, settings
):
    settings.analysis_chunk_chars = 4000
    job = await job_with_change(db, blobs, protocol, settings, after="small change\n" * 400)
    api = FakeAPI(cross_novel=True)
    await process_job(db, blobs, api, job, settings)
    calls = await db.rows("SELECT call_key FROM model_calls ORDER BY call_key")
    assert {"screen:connections", "deep:connections"} <= {c["call_key"] for c in calls}
    assert (await db.rows("SELECT result FROM reports"))[0]["result"]["_pipeline"][
        "deep_reason"
    ].startswith("cross-chunk")


async def test_screen_must_account_for_every_supplied_event():
    contract = request_contract(
        payload(), {"events": [{"id": "a", "diff": "+a"}, {"id": "b", "diff": "+b"}]}, True
    )
    api = FakeAPI()
    raw = json.loads((await api.create(**contract["request"])).model_dump_json())
    result = json.loads(raw["output"][0]["content"][0]["text"])
    result["reviewed_event_ids"] = ["e1"]
    raw["output"][0]["content"][0]["text"] = canonical(result)
    with pytest.raises(ValueError, match="every event"):
        parse_response(raw, contract, True)


@pytest.mark.postgres
async def test_tiered_focus_waits_for_general_and_cannot_enter_its_input(
    db, blobs, protocol, settings
):
    job = await job_with_change(db, blobs, protocol, settings)
    focused_payload = {**job["payload"], "kind": "focus", "focus": ["Old product objective"]}
    await db.execute(
        "INSERT INTO jobs(id,protocol_id,kind,parent_id,payload) VALUES('focus-test',:protocol,'focus',:parent,CAST(:payload AS jsonb))",
        protocol=protocol.id,
        parent=job["id"],
        payload=canonical(focused_payload),
    )
    assert await claim_job(db) is None
    api = FakeAPI()
    await process_job(db, blobs, api, job, settings)
    original = (await db.rows("SELECT result FROM reports WHERE id=:id", id=job["id"]))[0]["result"]
    focus = await claim_job(db)
    assert focus["kind"] == "focus"
    await process_job(db, blobs, api, focus, settings)
    assert api.models == ["gpt-5.6-luna", "gpt-5.6-sol"]
    requests = api.client.responses.create.call_args_list
    assert "Old product objective" not in canonical(requests[0].kwargs)
    assert "Old product objective" in canonical(requests[1].kwargs)
    assert (await db.rows("SELECT result FROM reports WHERE id=:id", id=job["id"]))[0][
        "result"
    ] == original
