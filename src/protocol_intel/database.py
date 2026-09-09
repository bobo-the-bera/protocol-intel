"""Short Postgres transactions fence workers and preserve transition occurrences."""

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from protocol_intel.config import Protocol, Settings, Source, canonical, digest


def uid() -> str:
    return str(uuid4())


class Database:
    def __init__(self, url: str):
        if not url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("Set DATABASE_URL to a PostgreSQL connection string")
        self.engine = create_async_engine(
            url.replace("postgresql://", "postgresql+psycopg://", 1),
            hide_parameters=True,
            pool_pre_ping=True,
        )

    async def close(self):
        await self.engine.dispose()

    async def rows(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            return [dict(row) for row in (await conn.execute(text(sql), params)).mappings()]

    async def execute(self, sql: str, **params: Any):
        async with self.engine.begin() as conn:
            await conn.execute(text(sql), params)

    async def add_source(self, conn: AsyncConnection, source: Source):
        await conn.execute(
            text("""
          INSERT INTO sources(id, spec, host, interval_seconds, max_interval_seconds, pinned)
          VALUES(:id, CAST(:spec AS jsonb), :host, :interval, :maximum, :pinned)
          ON CONFLICT(id) DO UPDATE SET
            interval_seconds=LEAST(sources.interval_seconds, excluded.interval_seconds),
            max_interval_seconds=LEAST(sources.max_interval_seconds, excluded.max_interval_seconds),
            pinned=sources.pinned OR excluded.pinned
        """),
            {
                "id": source.identity(),
                "spec": source.model_dump_json(),
                "host": urlsplit(source.url).hostname,
                "interval": source.interval_seconds,
                "maximum": max(source.interval_seconds, source.max_interval_seconds),
                "pinned": source.pinned,
            },
        )

    async def subscribe(self, conn: AsyncConnection, protocol: str, source_id: str):
        # Sharing a previously monitored source does not replay its history to a new subscriber.
        await conn.execute(
            text("""
          INSERT INTO subscriptions(protocol_id, source_id, baseline_sequence)
          SELECT :protocol, id, sequence FROM sources WHERE id=:source
          ON CONFLICT(protocol_id, source_id) DO UPDATE SET active=true
        """),
            {"protocol": protocol, "source": source_id},
        )

    async def sync(self, protocols: Iterable[Protocol]):
        async with self.engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(617429104)"))
            await conn.execute(text("UPDATE protocols SET enabled=false"))
            await conn.execute(text("UPDATE subscriptions SET active=false"))
            for protocol in protocols:
                await conn.execute(
                    text("""
                  INSERT INTO protocols(id,name,profile,analysis,enabled,config_hash)
                  VALUES(:id,:name,:profile,CAST(:analysis AS jsonb),:enabled,:hash)
                  ON CONFLICT(id) DO UPDATE SET name=excluded.name, profile=excluded.profile,
                    analysis=excluded.analysis, enabled=excluded.enabled, config_hash=excluded.config_hash
                """),
                    {
                        "id": protocol.id,
                        "name": protocol.name,
                        "profile": protocol.profile,
                        "analysis": protocol.analysis.model_dump_json(),
                        "enabled": protocol.enabled,
                        "hash": digest(protocol.model_dump_json().encode()),
                    },
                )
                for source in protocol.sources:
                    await self.add_source(conn, source)
                    await self.subscribe(conn, protocol.id, source.identity())
                    members = await conn.execute(
                        text("SELECT child_id FROM inventory_members WHERE root_id=:root"),
                        {"root": source.identity()},
                    )
                    for child in members.scalars():
                        # Inventory removals retain page polling; retirement is an explicit later policy.
                        await self.subscribe(conn, protocol.id, child)

    async def claim(self, limit: int, protocol: str | None = None, baseline: bool = False):
        async with self.engine.begin() as conn:
            result = await conn.execute(
                text("""
              SELECT s.* FROM sources s WHERE
                (lease_until IS NULL OR lease_until < now())
                AND next_check_at <= now()
                AND (NOT :baseline OR current_hash IS NULL)
                AND EXISTS (SELECT 1 FROM subscriptions sub JOIN protocols p ON p.id=sub.protocol_id
                  WHERE sub.source_id=s.id AND sub.active AND p.enabled
                  AND (CAST(:protocol AS text) IS NULL OR p.id=:protocol))
              ORDER BY next_check_at, id FOR UPDATE OF s SKIP LOCKED LIMIT :limit
            """),
                {"baseline": baseline, "protocol": protocol, "limit": limit},
            )
            states = [dict(row) for row in result.mappings()]
            for state in states:
                state["lease_token"] = uid()
                await conn.execute(
                    text(
                        "UPDATE sources SET lease_token=:token, lease_until=now()+interval '10 minutes' WHERE id=:id"
                    ),
                    {"id": state["id"], "token": state["lease_token"]},
                )
            return states

    async def heartbeat(self, state: dict):
        await self.execute(
            "UPDATE sources SET lease_until=now()+interval '10 minutes' WHERE id=:id AND lease_token=:token AND lease_until>now()",
            id=state["id"],
            token=state["lease_token"],
        )

    async def finish(
        self,
        state: dict,
        raw: str | None,
        normalized: str | None,
        metadata: dict,
        children: list[Source] | None = None,
    ):
        async with self.engine.begin() as conn:
            current = (
                (
                    await conn.execute(
                        text(
                            "SELECT *,lease_until>now() AS lease_valid FROM sources WHERE id=:id FOR UPDATE"
                        ),
                        {"id": state["id"]},
                    )
                )
                .mappings()
                .one()
            )
            # Late completions retain evidence references but cannot advance current state.
            if current["lease_token"] != state["lease_token"] or not current["lease_valid"]:
                await conn.execute(
                    text(
                        "INSERT INTO checks(id,source_id,outcome,metadata) VALUES(:id,:source,'STALE',CAST(:meta AS jsonb))"
                    ),
                    {
                        "id": uid(),
                        "source": state["id"],
                        "meta": canonical(
                            {"raw_hash": raw, "normalized_hash": normalized, **metadata}
                        ),
                    },
                )
                return "STALE"
            normalized = normalized or current["current_hash"]
            if normalized is None:
                raise ValueError("304 cannot establish a baseline")
            changed = normalized != current["current_hash"]
            outcome = (
                "BASELINE"
                if changed and current["current_hash"] is None
                else ("CHANGED" if changed else "UNCHANGED")
            )
            sequence = current["sequence"] + int(changed)
            if changed:
                await conn.execute(
                    text(
                        "INSERT INTO versions(source_id,sequence,normalized_hash,raw_hash,metadata) VALUES(:source,:seq,:hash,:raw,CAST(:meta AS jsonb))"
                    ),
                    {
                        "source": state["id"],
                        "seq": sequence,
                        "hash": normalized,
                        "raw": raw,
                        "meta": canonical(metadata),
                    },
                )
                await conn.execute(
                    text("""
                  INSERT INTO events(id,source_id,sequence,kind,baseline,old_hash,new_hash,metadata)
                  VALUES(:id,:source,:seq,:kind,:baseline,:old,:new,CAST(:meta AS jsonb))
                """),
                    {
                        "id": uid(),
                        "source": state["id"],
                        "seq": sequence,
                        "kind": "SITEMAP_CHANGED" if children is not None else "PAGE_MODIFIED",
                        "baseline": outcome == "BASELINE",
                        "old": current["current_hash"],
                        "new": normalized,
                        "meta": canonical(metadata),
                    },
                )
            if children is not None:
                await conn.execute(
                    text("UPDATE inventory_members SET active=false WHERE root_id=:id"),
                    {"id": state["id"]},
                )
                subscribers = (
                    (
                        await conn.execute(
                            text(
                                "SELECT protocol_id FROM subscriptions WHERE source_id=:id AND active"
                            ),
                            {"id": state["id"]},
                        )
                    )
                    .scalars()
                    .all()
                )
                for child in children:
                    await self.add_source(conn, child)
                    await conn.execute(
                        text(
                            "INSERT INTO inventory_members(root_id,child_id) VALUES(:root,:child) ON CONFLICT(root_id,child_id) DO UPDATE SET active=true"
                        ),
                        {"root": state["id"], "child": child.identity()},
                    )
                    for subscriber in subscribers:
                        await self.subscribe(conn, subscriber, child.identity())
            await conn.execute(
                text("""
              UPDATE sources SET sequence=:seq, current_hash=:hash,
                raw_hash=COALESCE(:raw,raw_hash), representation_url=:url,
                etag=:etag,last_modified=:modified,last_checked_at=now(),last_success_at=now(),
                last_changed_at=CASE WHEN :changed THEN now() ELSE last_changed_at END,
                stable_since=CASE WHEN :changed OR failures>0 OR stable_since IS NULL THEN now() ELSE stable_since END,
                failures=0,last_error=NULL,lease_token=NULL,lease_until=NULL,
                next_check_at=now()+make_interval(secs => CASE WHEN :changed OR pinned OR failures>0 THEN interval_seconds
                  WHEN stable_since < now()-interval '90 days' THEN LEAST(max_interval_seconds, interval_seconds*24)
                  WHEN stable_since < now()-interval '30 days' THEN LEAST(max_interval_seconds, interval_seconds*6)
                  WHEN stable_since < now()-interval '7 days' THEN LEAST(max_interval_seconds, interval_seconds*3)
                  ELSE interval_seconds END)
              WHERE id=:id
            """),
                {
                    "id": state["id"],
                    "seq": sequence,
                    "hash": normalized,
                    "raw": raw if changed else None,
                    "changed": changed,
                    "url": metadata["url"],
                    "etag": metadata.get("etag"),
                    "modified": metadata.get("last_modified"),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO checks(id,source_id,outcome,metadata) VALUES(:id,:source,:outcome,CAST(:meta AS jsonb))"
                ),
                {
                    "id": uid(),
                    "source": state["id"],
                    "outcome": outcome,
                    "meta": canonical(metadata),
                },
            )
            return outcome

    async def fail(self, state: dict, error: str, retry_seconds: int = 60):
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO checks(id,source_id,outcome,error) VALUES(:id,:source,'FAILED_TO_CHECK',:error)"
                ),
                {"id": uid(), "source": state["id"], "error": error},
            )
            await conn.execute(
                text("""
              UPDATE sources SET failures=failures+1,last_error=:error,last_checked_at=now(),
                lease_token=NULL,lease_until=NULL,
                next_check_at=now()+make_interval(secs => GREATEST(:retry, LEAST(86400,60*power(2,LEAST(failures,10)))::int))
              WHERE id=:id AND lease_token=:token AND lease_until>now()
            """),
                {
                    "id": state["id"],
                    "token": state["lease_token"],
                    "error": error,
                    "retry": retry_seconds,
                },
            )

    async def permit(self, host: str, settings: Settings) -> str | None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO origins(host) VALUES(:host) ON CONFLICT DO NOTHING"),
                {"host": host},
            )
            ready = (
                await conn.execute(
                    text(
                        "SELECT next_start_at<=now() AS ready FROM origins WHERE host=:host FOR UPDATE"
                    ),
                    {"host": host},
                )
            ).scalar()
            await conn.execute(
                text("DELETE FROM permits WHERE host=:host AND expires_at<now()"), {"host": host}
            )
            count = (
                await conn.execute(
                    text("SELECT count(*) FROM permits WHERE host=:host"), {"host": host}
                )
            ).scalar_one()
            if not ready or count >= settings.per_host_concurrency:
                return None
            token = uid()
            await conn.execute(
                text("INSERT INTO permits VALUES(:id,:host,now()+interval '2 minutes')"),
                {"id": token, "host": host},
            )
            await conn.execute(
                text(
                    "UPDATE origins SET next_start_at=now()+make_interval(secs => :spacing) WHERE host=:host"
                ),
                {"host": host, "spacing": settings.per_host_spacing_seconds},
            )
            return token

    async def release_permit(self, token: str):
        await self.execute("DELETE FROM permits WHERE id=:id", id=token)

    async def cooldown(self, host: str, seconds: int):
        await self.execute(
            "UPDATE origins SET next_start_at=GREATEST(next_start_at,now()+make_interval(secs=>:seconds)) WHERE host=:host",
            host=host,
            seconds=seconds,
        )
