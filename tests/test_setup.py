import io
from unittest.mock import AsyncMock

import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from protocol_intel.blobs import LocalBlobs, S3Blobs, blob_key
from protocol_intel.config import Settings
from protocol_intel.database import Database
from protocol_intel.setup import check_storage, storage_identity


@pytest.fixture
def s3():
    store = S3Blobs(
        Settings(
            _env_file=None,
            s3_bucket="setup-test",
            s3_region="us-east-1",
            aws_access_key_id="TEST_ONLY",
            aws_secret_access_key="TEST_ONLY",
        )
    )
    with Stubber(store.client) as stub:
        yield store, stub
        stub.assert_no_pending_responses()
    store.client.close()


@pytest.mark.parametrize("behavior", ["correct", "ignored", "corrupted", "denied"])
async def test_conditional_storage_check_rejects_broken_backends(s3, behavior):
    store, stub = s3
    stub.add_response(
        "put_object", {}, {"Bucket": "setup-test", "Key": ANY, "Body": ANY, "IfNoneMatch": "*"}
    )
    if behavior == "ignored":
        stub.add_response("put_object", {})
        with pytest.raises(ValueError, match="did not reject"):
            await store.check_conditional_writes()
        return
    if behavior == "denied":
        stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
        from botocore.exceptions import ClientError

        with pytest.raises(ClientError):
            await store.check_conditional_writes()
        return
    stub.add_client_error(
        "put_object", service_error_code="PreconditionFailed", http_status_code=412
    )
    body = (
        b"Protocol Intelligence Monitor conditional-write check\n"
        if behavior == "correct"
        else b"unexpected replacement"
    )
    stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(body), len(body))})
    if behavior == "corrupted":
        with pytest.raises(ValueError, match="changed existing bytes"):
            await store.check_conditional_writes()
    else:
        await store.check_conditional_writes()


def test_storage_identity_excludes_credentials_and_distinguishes_buckets():
    a = Settings(_env_file=None, blob_backend="s3", s3_bucket="archive-a")
    assert storage_identity(a) == storage_identity(
        a.model_copy(update={"database_url": "changed", "aws_secret_access_key": "changed"})
    )
    assert storage_identity(a) != storage_identity(a.model_copy(update={"s3_bucket": "archive-b"}))


async def test_storage_check_survives_new_clients_without_creating_protocol_events(db, blobs):
    written = await check_storage(db, blobs, "local-test")
    reopened = Database(db.engine.url.render_as_string(hide_password=False))
    try:
        read = await check_storage(reopened, LocalBlobs(blobs.root), "local-test", verify_only=True)
        assert read["probe_id"] == written["probe_id"]
        assert read["mode"] == "persisted_readback"
        assert await reopened.rows("SELECT id FROM events") == []
        assert await reopened.rows("SELECT id FROM jobs") == []
        assert await reopened.rows("SELECT id FROM outbox") == []
    finally:
        await reopened.close()


async def test_missing_and_corrupt_persisted_probes_fail(db, blobs):
    with pytest.raises(ValueError, match="No storage check"):
        await check_storage(db, blobs, "local-test", verify_only=True)
    written = await check_storage(db, blobs, "local-test")
    with pytest.raises(ValueError, match="No storage check"):
        await check_storage(db, blobs, "different-backend", verify_only=True)
    (blobs.root / blob_key(written["archive_hash"])).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        await check_storage(db, blobs, "local-test", verify_only=True)


async def test_failed_archive_cannot_commit_successful_probe(db, blobs, monkeypatch):
    monkeypatch.setattr(blobs, "get", AsyncMock(side_effect=OSError("readback failed")))
    with pytest.raises(OSError, match="readback failed"):
        await check_storage(db, blobs, "local-test")
    assert await db.rows("SELECT id FROM storage_probes") == []
