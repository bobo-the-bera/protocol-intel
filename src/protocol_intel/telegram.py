"""Outbound Telegram delivery distinguishes definite failure from uncertain sends."""

from typing import Any

import httpx
from sqlalchemy import text

from protocol_intel.blobs import BlobStore
from protocol_intel.config import Settings
from protocol_intel.database import Database


class DeliveryError(Exception):
    def __init__(self, message: str, retry_after: int = 60, permanent: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


class UncertainDelivery(Exception):
    """The provider might have posted; automatic resending could duplicate it."""


class Telegram:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        token = settings.telegram_bot_token.get_secret_value()
        if not token:
            raise ValueError("Set TELEGRAM_BOT_TOKEN in secrets before using Telegram commands")
        self.base = f"https://api.telegram.org/bot{token}/"
        self.destination = settings.telegram_chat_id
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10))

    async def close(self):
        await self.client.aclose()

    async def call(
        self, method: str, data: dict | None = None, files: dict | None = None, write: bool = False
    ) -> Any:
        try:
            if files:
                response = await self.client.post(self.base + method, data=data, files=files)
            else:
                response = await self.client.post(self.base + method, json=data or {})
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise DeliveryError("Telegram connection failed before submission") from None
        except httpx.HTTPError:
            if write:
                raise UncertainDelivery(
                    "Telegram send outcome is unknown; check the channel before retrying"
                ) from None
            raise DeliveryError("Telegram diagnostic request failed") from None
        if response.status_code >= 500:
            if write:
                raise UncertainDelivery("Telegram returned a server error after submission")
            raise DeliveryError("Telegram server is temporarily unavailable")
        try:
            body = response.json()
        except ValueError:
            if write:
                raise UncertainDelivery(
                    "Telegram returned an unreadable send acknowledgement"
                ) from None
            raise DeliveryError("Telegram diagnostic response was not JSON") from None
        if not body.get("ok"):
            code = body.get("error_code", response.status_code)
            delay = int(body.get("parameters", {}).get("retry_after", 60))
            # Avoid echoing provider descriptions or request URLs containing a bot token.
            raise DeliveryError(
                f"Telegram API error {code}; verify token, destination and post permission",
                delay,
                code in {400, 401, 403, 404},
            )
        return body["result"]

    async def check(self, destination: str | None = None) -> dict:
        target = destination or self.destination
        if not target:
            raise ValueError("Set TELEGRAM_CHAT_ID or run telegram-discover first")
        bot = await self.call("getMe")
        chat = await self.call("getChat", {"chat_id": target})
        if chat.get("type") != "channel":
            raise DeliveryError("Configured destination is not a Telegram channel", permanent=True)
        member = await self.call("getChatMember", {"chat_id": chat["id"], "user_id": bot["id"]})
        if member.get("status") != "administrator" or not member.get("can_post_messages"):
            raise DeliveryError(
                "Bot must be a channel administrator with Post Messages enabled", permanent=True
            )
        return {
            "bot": "@" + bot["username"],
            "channel_id": str(chat["id"]),
            "channel_type": chat["type"],
            "can_post_messages": True,
        }

    async def discover(self) -> list[dict]:
        webhook = await self.call("getWebhookInfo")
        if webhook.get("url"):
            raise ValueError(
                "This bot already uses a webhook. Use a dedicated bot; no webhook was modified"
            )
        updates = await self.call(
            "getUpdates", {"timeout": 0, "allowed_updates": ["channel_post", "my_chat_member"]}
        )
        candidates = {}
        for update in updates:
            post = update.get("channel_post", {})
            if (
                post.get("text", "").strip() == "protocol-intel setup"
                and post.get("chat", {}).get("type") == "channel"
            ):
                chat = post["chat"]
                candidates[str(chat["id"])] = {
                    "channel_id": str(chat["id"]),
                    "setup_marker_found": True,
                }
        # Only IDs for matching setup markers are printed, never channel message contents.
        return list(candidates.values())

    async def test(self) -> dict:
        checked = await self.check()
        message = await self.call(
            "sendMessage",
            {
                "chat_id": checked["channel_id"],
                "text": "Protocol Intelligence Monitor — Telegram connection verified.\n\nThis is a setup test, not a detected protocol change.",
                "link_preview_options": {"is_disabled": True},
            },
            write=True,
        )
        return {**checked, "message_id": message["message_id"], "test_sent": True}


def summary_text(row: dict) -> str:
    result = row["result"]
    lines = [f"PROTOCOL INTELLIGENCE — {row['protocol_id']}", f"Report {row['report_id']}", ""]
    for finding in result["findings"]:
        if finding["importance"] == "LOW":
            continue
        lines.extend(
            [
                f"[{finding['importance']}] {finding['title']}",
                finding["observed_change"],
                "Why: " + finding["significance"],
                "Uncertainty: " + finding["uncertainty"],
                "",
            ]
        )
    value = "\n".join(lines)
    # The attached report carries full evidence; the notification stays below Telegram's text limit.
    return (
        value.encode("utf-16-le")[:6800].decode("utf-16-le", errors="ignore")
        + "\nFull evidence is in the accompanying report."
    )


async def deliver_one(db: Database, blobs: BlobStore, telegram: Telegram) -> bool:
    async with db.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE outbox SET status='UNKNOWN',last_error='Worker stopped during submission; inspect channel before resolving' WHERE status='SENDING' AND sending_at<now()-interval '5 minutes'"
            )
        )
        found = (
            (
                await conn.execute(
                    text("""
          SELECT o.*,r.result,r.blob_hash,r.protocol_id FROM outbox o JOIN reports r ON r.id=o.report_id
          WHERE o.status='PENDING' AND o.next_attempt_at<=now()
            AND (o.part='summary' OR EXISTS(SELECT 1 FROM outbox s WHERE s.report_id=o.report_id AND s.destination=o.destination AND s.part='summary' AND s.status='SENT'))
          ORDER BY o.next_attempt_at,CASE WHEN o.part='summary' THEN 0 ELSE 1 END,o.id
          FOR UPDATE OF o SKIP LOCKED LIMIT 1
        """)
                )
            )
            .mappings()
            .first()
        )
        if found is None:
            return False
        row = dict(found)
        await conn.execute(
            text(
                "UPDATE outbox SET status='SENDING',sending_at=now(),attempts=attempts+1 WHERE id=:id"
            ),
            {"id": row["id"]},
        )
    submitted = False
    try:
        if row["destination"] != telegram.destination:
            raise DeliveryError(
                "Destination changed since this report was queued; operator review required",
                permanent=True,
            )
        checked = await telegram.check(row["destination"])
        if row["part"] == "summary":
            submitted = True
            message = await telegram.call(
                "sendMessage",
                {
                    "chat_id": checked["channel_id"],
                    "text": summary_text(row),
                    "link_preview_options": {"is_disabled": True},
                },
                write=True,
            )
        else:
            report = await blobs.get(row["blob_hash"])
            submitted = True
            message = await telegram.call(
                "sendDocument",
                {
                    "chat_id": checked["channel_id"],
                    "caption": f"Full evidence — {row['protocol_id']} — {row['report_id']}",
                },
                files={
                    "document": (
                        f"{row['protocol_id']}-{row['report_id']}.md",
                        report,
                        "text/markdown",
                    )
                },
                write=True,
            )
        await db.execute(
            "UPDATE outbox SET status='SENT',message_id=:message,last_error=NULL WHERE id=:id",
            id=row["id"],
            message=message["message_id"],
        )
    except UncertainDelivery:
        await db.execute(
            "UPDATE outbox SET status='UNKNOWN',last_error='Ambiguous Telegram submission; verify channel before resolving' WHERE id=:id",
            id=row["id"],
        )
    except DeliveryError as exc:
        await db.execute(
            "UPDATE outbox SET status=:status,last_error=:error,next_attempt_at=now()+make_interval(secs=>:delay) WHERE id=:id",
            id=row["id"],
            status="FAILED" if exc.permanent or row["attempts"] >= 4 else "PENDING",
            error=str(exc),
            delay=exc.retry_after,
        )
    except Exception:
        # This includes a database outage after a successful send; do not assume the post failed.
        await db.execute(
            "UPDATE outbox SET status=:status,last_error=:error,next_attempt_at=now()+interval '1 minute' WHERE id=:id",
            id=row["id"],
            status="UNKNOWN" if submitted else ("FAILED" if row["attempts"] >= 4 else "PENDING"),
            error="Delivery interrupted after submission; verify channel before resolving"
            if submitted
            else "Delivery preparation failed before submission",
        )
        raise
    return True
