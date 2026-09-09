"""Prove that database records and archived bytes survive separate setup processes."""

from uuid import uuid4

from protocol_intel.blobs import BlobStore, S3Blobs
from protocol_intel.config import Settings, canonical, digest
from protocol_intel.database import Database


def storage_identity(settings: Settings) -> str:
    # Match probes to their backend without storing connection credentials.
    location = (
        [settings.s3_endpoint_url, settings.s3_region, settings.s3_bucket]
        if settings.blob_backend == "s3"
        else [str(settings.blob_dir.resolve())]
    )
    return digest(canonical([settings.blob_backend, location]).encode())


def probe_body(probe_id: str) -> bytes:
    return canonical(
        {"purpose": "Protocol Intelligence Monitor storage check", "probe_id": probe_id}
    ).encode()


async def check_storage(
    db: Database, blobs: BlobStore, identity: str, verify_only: bool = False
) -> dict:
    if verify_only:
        rows = await db.rows(
            "SELECT id,blob_hash FROM storage_probes WHERE backend_identity=:identity ORDER BY created_at DESC,id DESC LIMIT 1",
            identity=identity,
        )
        if not rows:
            raise ValueError("No storage check found for this backend; run storage-check first")
        probe_id, key = rows[0]["id"], rows[0]["blob_hash"]
    else:
        # Confirm schema/connectivity before writing any diagnostic archive objects.
        await db.rows("SELECT id FROM storage_probes LIMIT 0")
        if isinstance(blobs, S3Blobs):
            await blobs.check_conditional_writes()
        probe_id = str(uuid4())
        payload = probe_body(probe_id)
        key = await blobs.put(payload)
        if key != digest(payload) or await blobs.get(key) != payload:
            raise ValueError("Archive write/readback did not preserve the diagnostic bytes")
        if await blobs.put(payload) != key or await blobs.get(key) != payload:
            raise ValueError("Archive duplicate-write/readback check failed")
        # Commit a pointer only after the archive has been read back successfully.
        await db.execute(
            "INSERT INTO storage_probes(id,backend_identity,blob_hash) VALUES(:id,:identity,:hash)",
            id=probe_id,
            identity=identity,
            hash=key,
        )

    if key != digest(probe_body(probe_id)) or await blobs.get(key) != probe_body(probe_id):
        raise ValueError("Stored diagnostic evidence could not be verified")
    return {
        "storage_check": "passed",
        "mode": "persisted_readback" if verify_only else "write_and_readback",
        "probe_id": probe_id,
        "archive_hash": key,
    }
