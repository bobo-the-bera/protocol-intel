import asyncio

import pytest
from pydantic import ValidationError

from protocol_intel.analysis import model_input, split_evidence
from protocol_intel.config import Analysis, Protocol, Source, canonical, load_protocols, public_url
from protocol_intel.extract import markdown_candidates, normalize
from protocol_intel.safety import safe_error


def test_no_thesis_is_required(tmp_path):
    directory = tmp_path / "protocols"
    directory.mkdir()
    (directory / "test.yaml").write_text(
        "id: test\nname: Test\nsources:\n  - id: docs\n    url: https://docs.example.org/\n"
    )
    protocol = load_protocols(tmp_path)[0]
    assert protocol.analysis == Analysis(mode="always_deep", focus=[])


def test_unknown_config_is_rejected():
    with pytest.raises(ValidationError):
        Source(id="docs", url="https://example.org", keywords_to_discard=["small"])


def test_optional_focus_does_not_change_source_identity(source):
    normal = Protocol(id="test", name="Test", sources=[source])
    focused = normal.model_copy(update={"analysis": Analysis(focus=["old product objective"])})
    assert normal.sources[0].identity() == focused.sources[0].identity()


def test_focus_cannot_leak_into_general_model_input():
    general = {
        "kind": "general",
        "protocol": "Test",
        "profile": "Neutral facts",
        "coverage": [],
        "prior": [],
    }
    assert canonical(model_input(general, {"events": []})) == canonical(
        model_input({**general, "focus": ["only look for old product"]}, {"events": []})
    )
    assert "optional_watch_questions" in model_input(
        {**general, "kind": "focus", "focus": ["old product"]}, {}
    )


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "https://user:password@example.org", "https://example.org:8000/path"],
)
def test_invalid_source_urls(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_url_identity_preserves_information():
    assert (
        public_url("https://EXAMPLE.org/API?mode=perps#example")
        == "https://example.org/API?mode=perps"
    )


def test_markdown_candidate_must_be_expected_origin():
    body = '<a href="https://github.com/org/repo/edit/main/doc.md">Edit</a><a href="/guide.md">Markdown</a>'
    assert markdown_candidates(body, "https://docs.example.org/guide", []) == [
        "https://docs.example.org/guide.md"
    ]


def test_normalizer_preserves_one_line_api_change(source):
    before = normalize(
        '<html><body><pre>{"delegate": false}</pre></body></html>', "text/html", source, source.url
    )
    after = normalize(
        '<html><body><pre>{"delegate": true}</pre></body></html>', "text/html", source, source.url
    )
    assert before != after
    assert '"delegate": true' in after


def test_links_tables_and_embedded_configuration_survive(source):
    value = normalize(
        '<html><body><a href="/perps">Trade</a><table><tr><td>collateral</td><td>stakedHype</td></tr></table><script type="application/json">{"portfolioMarginEnabled":true}</script></body></html>',
        "text/html",
        source,
        source.url,
    )
    for expected in ("https://docs.example.org/perps", "stakedHype", "portfolioMarginEnabled"):
        assert expected in value


def test_only_explicit_noise_rules_remove_content(source):
    configured = source.model_copy(update={"ignore_patterns": [r"Last updated \d+ days? ago"]})
    a = "# API\ndelegateAddress\nLast updated 1 day ago\n"
    b = "# API\ndelegateAddress\nLast updated 2 days ago\n"
    assert normalize(a, "text/plain", configured, source.url) == normalize(
        b, "text/plain", configured, source.url
    )
    assert normalize(a, "text/plain", source, source.url) != normalize(
        b, "text/plain", source, source.url
    )


def test_empty_extraction_is_failure(source):
    with pytest.raises(ValueError, match="Empty extraction"):
        normalize("<html><body></body></html>", "text/html", source, source.url)


def test_chunking_drops_no_characters_or_small_events():
    huge = "+" + "a" * 12000 + "\n"
    tiny = "+portfolioMarginEnabled: true\n"
    events = [{"id": "large", "diff": huge}, {"id": "tiny", "diff": tiny}]
    packed = split_evidence(events, 4000)
    pieces = [item for chunk in packed for item in chunk["events"]]
    assert "".join(p["diff"] for p in pieces if p["id"] == "large") == huge
    assert "".join(p["diff"] for p in pieces if p["id"] == "tiny") == tiny


async def test_concurrent_blob_writes_are_atomic_and_deduplicated(blobs):
    data = b"exact historical evidence\n"
    keys = await asyncio.gather(*(blobs.put(data) for _ in range(8)))
    assert len(set(keys)) == 1
    assert await blobs.get(keys[0]) == data
    assert len(list(blobs.root.glob("sha256/*/*"))) == 1


async def test_corrupt_blob_is_not_silently_used(blobs):
    key = await blobs.put(b"good")
    path = next(blobs.root.glob("sha256/*/*"))
    path.write_bytes(b"bad")
    with pytest.raises(ValueError, match="integrity"):
        await blobs.get(key)


def test_bot_tokens_are_redacted(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET_ABC")
    message = safe_error(
        ValueError("request https://api.telegram.org/bot123456:SECRET_ABC/sendMessage failed")
    )
    assert "SECRET_ABC" not in message


def test_database_credentials_are_redacted_without_environment(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    message = safe_error(ValueError("bad connection postgresql+psycopg://user:SECRET_ABC@host/db"))
    assert "SECRET_ABC" not in message
