"""Bounded public HTTP fetching keeps validators scoped to a representation."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from protocol_intel.config import Settings, public_url
from protocol_intel.database import Database


class FetchError(Exception):
    def __init__(self, message: str, retry_seconds: int = 60):
        super().__init__(message)
        self.retry_seconds = retry_seconds


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    headers: dict[str, str]

    @property
    def text(self) -> str:
        # Invalid text is a coverage failure, not a lossy replacement-character snapshot.
        # aiter_bytes already decompressed the body; inspect only charset metadata here.
        encoding = (
            httpx.Response(
                200, headers={"content-type": self.headers.get("content-type", "")}
            ).encoding
            or "utf-8"
        )
        return self.body.decode(encoding, errors="strict")


def retry_delay(value: str | None) -> int:
    try:
        return min(86400, max(1, int(value or "60")))
    except ValueError:
        try:
            return min(
                86400,
                max(
                    1, int((parsedate_to_datetime(value or "") - datetime.now(UTC)).total_seconds())
                ),
            )
        except (ValueError, TypeError):
            return 60


async def validate_destination(url: str):
    host = urlsplit(public_url(url)).hostname
    records = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    if not records or any(not ipaddress.ip_address(record[4][0]).is_global for record in records):
        raise FetchError("Source resolved to a non-public destination")


class HTTP:
    def __init__(
        self,
        settings: Settings,
        database: Database | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.settings = settings
        self.db = database
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            headers={"User-Agent": "protocol-intel/0.1 (public technical change monitor)"},
            limits=httpx.Limits(max_connections=settings.http_concurrency),
        )
        self.slots = asyncio.Semaphore(settings.http_concurrency)
        self.host_slots: dict[str, asyncio.Semaphore] = {}

    async def close(self):
        await self.client.aclose()

    async def fetch(
        self, url: str, allowed_hosts: set[str], validators: dict | None = None
    ) -> Response:
        # Bound DNS, permit waits, redirects and slow-drip bodies below the two-minute permit lease.
        try:
            async with asyncio.timeout(self.settings.http_request_timeout_seconds):
                return await self._fetch(url, allowed_hosts, validators)
        except TimeoutError:
            raise FetchError("HTTP operation exceeded its total time limit") from None
        except httpx.HTTPError:
            raise FetchError("HTTP transport failed before a complete observation") from None

    async def _fetch(
        self, url: str, allowed_hosts: set[str], validators: dict | None = None
    ) -> Response:
        url = public_url(url)
        headers = dict(validators or {})
        for _ in range(6):
            host = urlsplit(url).hostname or ""
            if host not in allowed_hosts:
                raise FetchError("Redirect or discovered URL left the configured host scope")
            await validate_destination(url)
            host_slot = self.host_slots.setdefault(
                host, asyncio.Semaphore(self.settings.per_host_concurrency)
            )
            async with self.slots, host_slot:
                permit = None
                if self.db:
                    while permit is None:
                        permit = await self.db.permit(host, self.settings)
                        if permit is None:
                            await asyncio.sleep(0.5)
                try:
                    async with self.client.stream("GET", url, headers=headers) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if "location" not in response.headers:
                                raise FetchError("Redirect lacked a destination")
                            url = public_url(urljoin(url, response.headers["location"]))
                            headers = {}  # Validators belong to the original representation URL.
                            continue
                        if response.status_code == 304:
                            return Response(url, 304, b"", dict(response.headers))
                        if response.status_code != 200:
                            delay = retry_delay(response.headers.get("retry-after"))
                            if self.db and response.status_code in {429, 503}:
                                await self.db.cooldown(host, delay)
                            raise FetchError(
                                f"HTTP {response.status_code} while checking {host}", delay
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.settings.max_body_bytes:
                                raise FetchError(
                                    "Body exceeded configured size; coverage is incomplete"
                                )
                        content_type = response.headers.get("content-type", "").lower()
                        if content_type and not any(
                            t in content_type for t in ("text/", "json", "xml", "markdown")
                        ):
                            raise FetchError(
                                "Unsupported content type; no text snapshot was created"
                            )
                        if response.headers.get("cf-mitigated") == "challenge":
                            raise FetchError("Source returned an access challenge")
                        return Response(url, 200, bytes(body), dict(response.headers))
                finally:
                    if self.db and permit:
                        await self.db.release_permit(permit)
        raise FetchError("Redirect limit exceeded")
