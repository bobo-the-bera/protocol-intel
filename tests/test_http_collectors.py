import pytest

from protocol_intel.collector import discover, fetch_page
from protocol_intel.config import Source
from protocol_intel.http import HTTP, FetchError


@pytest.mark.usefixtures("public_dns")
async def test_same_host_markdown_is_preferred(settings, respx_mock):
    source = Source(id="guide", url="https://docs.example.org/guide")
    respx_mock.get(source.url).respond(
        200,
        text='<html><a href="/guide.md">Markdown</a></html>',
        headers={"content-type": "text/html"},
    )
    respx_mock.get("https://docs.example.org/guide.md").respond(
        200, text="# Actual API\ndelegateAddress", headers={"content-type": "text/markdown"}
    )
    http = HTTP(settings)
    try:
        result = await fetch_page(http, source, {})
        assert result.url.endswith("guide.md")
        assert "delegateAddress" in result.text
    finally:
        await http.close()


@pytest.mark.usefixtures("public_dns")
async def test_false_markdown_endpoint_falls_back(settings, respx_mock):
    source = Source(id="guide", url="https://docs.example.org/guide")
    body = '<html><body>Real docs <a href="/guide.md">Markdown</a></body></html>'
    respx_mock.get(source.url).respond(200, text=body, headers={"content-type": "text/html"})
    respx_mock.get("https://docs.example.org/guide.md").respond(
        200, text="<!doctype html><html>Not Markdown</html>", headers={"content-type": "text/plain"}
    )
    http = HTTP(settings)
    try:
        result = await fetch_page(http, source, {})
        assert result.url == source.url
        assert "Real docs" in result.text
    finally:
        await http.close()


@pytest.mark.usefixtures("public_dns")
async def test_validators_are_sent_to_the_stored_representation(settings, respx_mock):
    url = "https://docs.example.org/guide.md"
    route = respx_mock.get(url, headers={"If-None-Match": '"old"'}).respond(304)
    http = HTTP(settings)
    try:
        result = await fetch_page(
            http,
            Source(id="guide", url="https://docs.example.org/guide"),
            {"representation_url": url, "etag": '"old"'},
        )
        assert route.called and result.status == 304
    finally:
        await http.close()


@pytest.mark.usefixtures("public_dns")
async def test_partial_sitemap_is_failure_not_removal(settings, respx_mock):
    source = Source(id="docs", kind="sitemap", url="https://docs.example.org/sitemap.xml")
    respx_mock.get(source.url).respond(
        200,
        text="<sitemapindex><sitemap><loc>https://docs.example.org/child.xml</loc></sitemap></sitemapindex>",
    )
    respx_mock.get("https://docs.example.org/child.xml").respond(503)
    http = HTTP(settings)
    try:
        with pytest.raises(FetchError, match="HTTP 503"):
            await discover(http, source)
    finally:
        await http.close()


@pytest.mark.usefixtures("public_dns")
async def test_sitemap_limit_is_not_silent_truncation(settings, respx_mock):
    source = Source(
        id="docs", kind="sitemap", url="https://docs.example.org/sitemap.xml", max_pages=1
    )
    respx_mock.get(source.url).respond(
        200,
        text="<urlset><url><loc>https://docs.example.org/a</loc></url><url><loc>https://docs.example.org/b</loc></url></urlset>",
    )
    http = HTTP(settings)
    try:
        with pytest.raises(FetchError, match="max_pages"):
            await discover(http, source)
    finally:
        await http.close()


@pytest.mark.usefixtures("public_dns")
async def test_redirect_scope_is_enforced(settings, respx_mock):
    respx_mock.get("https://docs.example.org/").respond(
        302, headers={"location": "http://127.0.0.1/secret"}
    )
    http = HTTP(settings)
    try:
        with pytest.raises(FetchError, match="scope"):
            await http.fetch("https://docs.example.org/", {"docs.example.org"})
    finally:
        await http.close()


@pytest.mark.usefixtures("public_dns")
async def test_oversized_body_fails(settings, respx_mock):
    settings.max_body_bytes = 1024
    respx_mock.get("https://docs.example.org/").respond(200, content=b"x" * 2048)
    http = HTTP(settings)
    try:
        with pytest.raises(FetchError, match="size"):
            await http.fetch("https://docs.example.org/", {"docs.example.org"})
    finally:
        await http.close()
