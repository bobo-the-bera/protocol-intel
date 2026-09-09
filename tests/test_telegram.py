import httpx
import pytest

from protocol_intel.telegram import DeliveryError, Telegram, UncertainDelivery, summary_text

BASE = "https://api.telegram.org/bot123456:TEST_ONLY/"


def allowed_bot(respx_mock):
    respx_mock.post(BASE + "getMe").respond(
        200, json={"ok": True, "result": {"id": 123456, "username": "monitor_test_bot"}}
    )
    respx_mock.post(BASE + "getChat").respond(
        200, json={"ok": True, "result": {"id": -1001234567890, "type": "channel"}}
    )
    return respx_mock.post(BASE + "getChatMember").respond(
        200, json={"ok": True, "result": {"status": "administrator", "can_post_messages": True}}
    )


async def test_check_is_read_only_and_test_sends_one_labeled_message(settings, respx_mock):
    allowed_bot(respx_mock)
    send = respx_mock.post(BASE + "sendMessage").respond(
        200, json={"ok": True, "result": {"message_id": 42}}
    )
    bot = Telegram(settings)
    try:
        assert (await bot.check())["can_post_messages"]
        assert send.call_count == 0
        assert (await bot.test())["message_id"] == 42
        assert send.call_count == 1
        assert b"not a detected protocol change" in send.calls[0].request.content
    finally:
        await bot.close()


async def test_missing_post_permission_prevents_send(settings, respx_mock):
    allowed_bot(respx_mock).respond(
        200, json={"ok": True, "result": {"status": "administrator", "can_post_messages": False}}
    )
    bot = Telegram(settings)
    try:
        with pytest.raises(DeliveryError, match="Post Messages"):
            await bot.test()
    finally:
        await bot.close()


async def test_private_channel_discovery_only_returns_matching_setup_ids(settings, respx_mock):
    respx_mock.post(BASE + "getWebhookInfo").respond(200, json={"ok": True, "result": {"url": ""}})
    respx_mock.post(BASE + "getUpdates").respond(
        200,
        json={
            "ok": True,
            "result": [
                {
                    "channel_post": {
                        "text": "private unrelated message",
                        "chat": {"type": "channel", "id": -1001},
                    }
                },
                {
                    "channel_post": {
                        "text": "protocol-intel setup",
                        "chat": {"type": "channel", "id": -1002},
                    }
                },
            ],
        },
    )
    bot = Telegram(settings)
    try:
        assert await bot.discover() == [{"channel_id": "-1002", "setup_marker_found": True}]
    finally:
        await bot.close()


async def test_existing_webhook_is_never_deleted(settings, respx_mock):
    respx_mock.post(BASE + "getWebhookInfo").respond(
        200, json={"ok": True, "result": {"url": "https://existing.example.org/bot"}}
    )
    bot = Telegram(settings)
    try:
        with pytest.raises(ValueError, match="No webhook was modified|no webhook was modified"):
            await bot.discover()
    finally:
        await bot.close()


async def test_rate_limit_is_definite_retry_and_timeout_is_uncertain(settings, respx_mock):
    route = respx_mock.post(BASE + "sendMessage").respond(
        429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 17}}
    )
    bot = Telegram(settings)
    try:
        with pytest.raises(DeliveryError) as error:
            await bot.call("sendMessage", {}, write=True)
        assert error.value.retry_after == 17
        route.mock(side_effect=httpx.ReadTimeout("provider did not acknowledge"))
        with pytest.raises(UncertainDelivery):
            await bot.call("sendMessage", {}, write=True)
    finally:
        await bot.close()


def test_summary_is_bounded_even_with_large_findings():
    finding = {
        "importance": "HIGH",
        "title": "new",
        "observed_change": "x" * 10000,
        "significance": "why",
        "uncertainty": "unknown",
    }
    assert (
        len(
            summary_text(
                {"result": {"findings": [finding]}, "protocol_id": "test", "report_id": "id"}
            )
        )
        <= 4096
    )
