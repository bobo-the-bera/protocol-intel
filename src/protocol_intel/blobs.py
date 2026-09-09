"""Content hashes address immutable evidence independently of its protocol."""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from protocol_intel.config import Settings, digest


class BlobStore(Protocol):
    async def put(self, data: bytes) -> str: ...
    async def get(self, key: str) -> bytes: ...


def blob_key(key: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("Invalid evidence hash")
    return f"sha256/{key[:2]}/{key}"


class LocalBlobs:
    def __init__(self, root: Path):
        self.root = root

    def _put(self, data: bytes) -> str:
        key = digest(data)
        path = self.root / blob_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic linking prevents a concurrent writer exposing an incomplete body.
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if digest(path.read_bytes()) != key:
                    raise ValueError("Existing evidence failed its integrity check") from None
        finally:
            Path(temporary).unlink(missing_ok=True)
        return key

    async def put(self, data: bytes) -> str:
        return await asyncio.to_thread(self._put, data)

    async def get(self, key: str) -> bytes:
        data = await asyncio.to_thread((self.root / blob_key(key)).read_bytes)
        if digest(data) != key:
            raise ValueError("Evidence failed its integrity check")
        return data


class S3Blobs:
    def __init__(self, settings: Settings):
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required for the s3 blob backend")
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.aws_access_key_id.get_secret_value() or None,
            aws_secret_access_key=settings.aws_secret_access_key.get_secret_value() or None,
        )
        self.slots = asyncio.Semaphore(8)

    async def put(self, data: bytes) -> str:
        key = digest(data)
        async with self.slots:
            try:
                await asyncio.to_thread(
                    self.client.put_object,
                    Bucket=self.bucket,
                    Key=blob_key(key),
                    Body=data,
                    IfNoneMatch="*",
                    ContentType="application/octet-stream",
                    Metadata={"sha256": key},
                )
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                    raise
        return key

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            response = self.client.get_object(Bucket=self.bucket, Key=blob_key(key))
            with response["Body"] as stream:
                return stream.read()

        async with self.slots:
            data = await asyncio.to_thread(read)
        if digest(data) != key:
            raise ValueError("Evidence failed its integrity check")
        return data


def open_blobs(settings: Settings) -> BlobStore:
    return S3Blobs(settings) if settings.blob_backend == "s3" else LocalBlobs(settings.blob_dir)
