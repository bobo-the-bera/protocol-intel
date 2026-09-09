"""Archive complete observations before making any semantic work eligible."""

import asyncio
import contextlib
from collections import Counter
from urllib.parse import urlsplit

from protocol_intel.blobs import BlobStore
from protocol_intel.config import Source, canonical
from protocol_intel.database import Database
from protocol_intel.extract import (
    EXTRACTOR_VERSION,
    is_html,
    markdown_candidates,
    normalize,
    sitemap_entries,
)
from protocol_intel.http import HTTP, FetchError, Response
from protocol_intel.safety import safe_error


def allowed(source: Source) -> set[str]:
    return {urlsplit(source.url).hostname or "", *source.allowed_hosts}


def validators(state: dict) -> dict[str, str]:
    result = {}
    if state.get("etag"):
        result["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        result["If-Modified-Since"] = state["last_modified"]
    return result


async def fetch_page(http: HTTP, source: Source, state: dict) -> Response:
    target = state.get("representation_url") or source.url
    try:
        response = await http.fetch(target, allowed(source), validators(state))
    except (FetchError, UnicodeError):
        if target == source.url or not source.prefer_markdown or source.kind != "page":
            raise
        # A vanished preferred representation must not permanently stop the canonical page check.
        response = await http.fetch(source.url, allowed(source))
    if response.status == 304:
        return response
    if source.prefer_markdown and source.kind == "page":
        content_type = response.headers.get("content-type", "")
        # A formerly valid Markdown endpoint can start serving HTML; compare the fallback explicitly.
        if target != source.url and is_html(response.text, content_type):
            response = await http.fetch(source.url, allowed(source))
        if is_html(response.text, response.headers.get("content-type", "")):
            for candidate in markdown_candidates(response.text, response.url, source.allowed_hosts):
                try:
                    markdown = await http.fetch(candidate, allowed(source))
                    if markdown.status == 200 and not is_html(
                        markdown.text, markdown.headers.get("content-type", "")
                    ):
                        return markdown
                except (FetchError, UnicodeError):
                    continue
    return response


async def discover(http: HTTP, source: Source) -> tuple[bytes, str, list[Source]]:
    pending, visited, urls, raw_documents = [source.url], set(), set(), {}
    while pending:
        target = pending.pop()
        if target in visited:
            continue
        visited.add(target)
        if len(visited) > source.max_sitemaps:
            raise FetchError("Sitemap count limit exceeded; previous inventory retained")
        # Recheck child sitemaps even if the index document itself did not change.
        response = await http.fetch(target, allowed(source))
        kind, entries = sitemap_entries(response.body)
        raw_documents[response.url] = response.text
        if kind == "sitemapindex":
            pending.extend(entries)
        else:
            for url in entries:
                if urlsplit(url).hostname not in allowed(source):
                    raise FetchError("Sitemap URL outside configured host scope")
                if not source.include_prefixes or any(
                    url.startswith(prefix) for prefix in source.include_prefixes
                ):
                    urls.add(url)
            if len(urls) > source.max_pages:
                raise FetchError("max_pages exceeded; previous inventory retained, no truncation")
    children = [
        source.model_copy(
            update={
                "id": "discovered",
                "kind": "page",
                "url": url,
                "interval_seconds": 3600,
                "include_prefixes": [],
                "pinned": False,
            }
        )
        for url in sorted(urls)
    ]
    return canonical(raw_documents).encode(), canonical(sorted(urls)) + "\n", children


async def _heartbeat(db: Database, state: dict):
    while True:
        await asyncio.sleep(30)
        await db.heartbeat(state)


async def collect_one(db: Database, blobs: BlobStore, http: HTTP, state: dict) -> str:
    source = Source.model_validate(state["spec"])
    heartbeat = asyncio.create_task(_heartbeat(db, state))
    try:
        children = None
        metadata: dict = {
            "url": source.url,
            "source_kind": source.kind,
            "extractor": EXTRACTOR_VERSION,
        }
        if source.kind == "sitemap":
            raw, normalized, children = await discover(http, source)
        else:
            response = await fetch_page(http, source, state)
            metadata.update(
                {
                    "url": response.url,
                    "etag": response.headers.get(
                        "etag", state.get("etag") if response.status == 304 else None
                    ),
                    "last_modified": response.headers.get(
                        "last-modified",
                        state.get("last_modified") if response.status == 304 else None,
                    ),
                    "http_status": response.status,
                    "content_type": response.headers.get("content-type", ""),
                }
            )
            if response.status == 304:
                return await db.finish(state, None, None, metadata)
            raw = response.body
            normalized = normalize(
                response.text, response.headers.get("content-type", ""), source, response.url
            )
        normalized_hash = await blobs.put(normalized.encode())
        # Mechanical-only drift need not duplicate raw storage after normalization proves no change.
        raw_hash = state.get("raw_hash")
        if normalized_hash != state.get("current_hash"):
            raw_hash = await blobs.put(raw)
        return await db.finish(state, raw_hash, normalized_hash, metadata, children)
    except Exception as exc:
        await db.fail(state, safe_error(exc), getattr(exc, "retry_seconds", 60))
        return "FAILED_TO_CHECK"
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat


async def run_due(
    db: Database,
    blobs: BlobStore,
    http: HTTP,
    limit: int = 100,
    protocol: str | None = None,
    baseline: bool = False,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    remaining = limit
    while remaining > 0:
        states = await db.claim(min(remaining, http.settings.http_concurrency), protocol, baseline)
        if not states:
            break
        results = await asyncio.gather(*(collect_one(db, blobs, http, state) for state in states))
        counts.update(results)
        remaining -= len(states)
    return dict(counts)
