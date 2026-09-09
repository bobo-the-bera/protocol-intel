import asyncio

import httpx
import pytest

from protocol_intel.analysis import Assessment, frozen_request
from protocol_intel.collector import fetch_page
from protocol_intel.config import Source, canonical
from protocol_intel.extract import markdown_candidates, normalize
from protocol_intel.http import HTTP, FetchError, retry_delay
from protocol_intel.telegram import summary_text


def test_markdown_link_to_other_page_cannot_replace_current_page():
    body = '<a href="/other.md">Another document</a><a href="/guide.md">This page</a>'
    assert markdown_candidates(body, "https://docs.example.org/guide", []) == [
        "https://docs.example.org/guide.md"
    ]


def test_explicit_markdown_alternate_is_allowed_but_external_edit_is_not():
    body = '<link rel="alternate" type="text/markdown" href="/representations/home.md"><a href="https://github.com/org/repo/edit/page.md">Edit</a>'
    assert markdown_candidates(body, "https://docs.example.org/", []) == [
        "https://docs.example.org/representations/home.md"
    ]


def test_table_cell_boundaries_cannot_hide_a_change(source):
    before = "<html><table><tr><td>a</td><td>bc</td></tr></table></html>"
    after = "<html><table><tr><td>ab</td><td>c</td></tr></table></html>"
    assert normalize(before, "text/html", source, source.url) != normalize(
        after, "text/html", source, source.url
    )


def test_extractor_upgrade_creates_a_new_baseline_identity(source, monkeypatch):
    original = source.identity()
    monkeypatch.setattr("protocol_intel.config.EXTRACTOR_VERSION", "future-version")
    assert source.identity() != original


@pytest.mark.usefixtures("public_dns")
@pytest.mark.parametrize("failure", [404, 503, "timeout"])
async def test_missing_stored_markdown_recovers_via_canonical_html(settings, respx_mock, failure):
    source = Source(id="guide", url="https://docs.example.org/guide")
    route = respx_mock.get(source.url + ".md")
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("upstream timeout"))
    else:
        route.respond(failure)
    respx_mock.get(source.url).respond(
        200, text="<html>Current docs</html>", headers={"content-type": "text/html"}
    )
    http = HTTP(settings)
    try:
        result = await fetch_page(
            http, source, {"representation_url": source.url + ".md", "etag": '"old"'}
        )
        assert result.url == source.url and "Current docs" in result.text
        assert "If-None-Match" not in respx_mock.calls[-1].request.headers
    finally:
        await http.close()


@pytest.mark.usefixtures("public_dns")
async def test_total_deadline_bounds_slow_bodies_and_releases_slots(settings, respx_mock):
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b"x"
                await asyncio.sleep(0.005)

    settings.http_request_timeout_seconds = 0.02
    respx_mock.get("https://docs.example.org/slow").mock(
        return_value=httpx.Response(200, stream=SlowBody())
    )
    respx_mock.get("https://docs.example.org/good").respond(200, text="complete")
    http = HTTP(settings)
    try:
        with pytest.raises(FetchError, match="total time"):
            await http.fetch("https://docs.example.org/slow", {"docs.example.org"})
        assert (
            await http.fetch("https://docs.example.org/good", {"docs.example.org"})
        ).text == "complete"
    finally:
        await http.close()


def test_retry_after_cannot_overflow_scheduler():
    assert retry_delay("999999999999999999999") == 86400
    assert retry_delay("-2") == 1


def test_retry_request_keeps_prompt_model_schema_and_output_budget(monkeypatch):
    payload = {
        "kind": "general",
        "protocol": "Test",
        "profile": "Neutral",
        "coverage": [],
        "model": "explicit-model",
        "effort": "high",
        "system_prompt": "Frozen original instructions",
        "response_schema": Assessment.model_json_schema(),
        "max_output_tokens": 16000,
    }
    original = frozen_request(payload, {"events": [{"id": "one", "diff": "+endpoint"}]})
    monkeypatch.setattr("protocol_intel.analysis.GENERAL", "New instructions after deployment")
    assert original == frozen_request(payload, {"events": [{"id": "one", "diff": "+endpoint"}]})
    for key, value in [
        ("model", "another-model"),
        ("effort", "low"),
        ("system_prompt", "changed"),
        ("max_output_tokens", 100),
    ]:
        assert canonical(original) != canonical(
            frozen_request(
                {**payload, key: value}, {"events": [{"id": "one", "diff": "+endpoint"}]}
            )
        )


def test_telegram_summary_stays_bounded_with_astral_unicode():
    row = {
        "protocol_id": "test",
        "report_id": "report",
        "result": {
            "findings": [
                {
                    "importance": "HIGH",
                    "title": "🚀" * 5000,
                    "observed_change": "x",
                    "significance": "y",
                    "uncertainty": "z",
                }
            ]
        },
    }
    message = summary_text(row)
    assert len(message.encode("utf-16-le")) // 2 < 4096
