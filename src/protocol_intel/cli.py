"""Operator commands make setup, baselines and delivery state inspectable."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Literal

import typer

from protocol_intel.analysis import (
    OpenAIAnalyzer,
    claim_job,
    process_job,
    queue_clusters,
    reconcile_jobs,
    retry_job,
)
from protocol_intel.blobs import open_blobs
from protocol_intel.collector import run_due
from protocol_intel.config import Settings, canonical, digest, load_protocols
from protocol_intel.database import Database, uid
from protocol_intel.digests import queue_daily_digest
from protocol_intel.http import HTTP
from protocol_intel.preview import generate_preview, inspect_preview
from protocol_intel.release import release_status
from protocol_intel.safety import safe_error
from protocol_intel.setup import check_storage, storage_identity
from protocol_intel.telegram import Telegram, deliver_one

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


def show(value):
    typer.echo(json.dumps(value, indent=2, default=str))


def run(coroutine):
    try:
        return asyncio.run(coroutine)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise typer.Exit(130) from None
    except Exception as exc:
        typer.echo(safe_error(exc), err=True)
        raise typer.Exit(1) from None


def configuration(protocol: str | None = None):
    settings = Settings()
    protocols = load_protocols(settings.config_dir)
    if protocol is not None and protocol not in {p.id for p in protocols}:
        raise ValueError(f"Unknown protocol: {protocol}")
    return settings, protocols


@app.command("release-check")
def release_check(
    target: Literal["first_production", "full_specification", "scale"] = "first_production",
    register: Path = Path("docs/RELEASE_REQUIREMENTS.yaml"),
):
    """Fail while the selected release gate has open or unverified requirements."""
    try:
        result = release_status(register, target)
    except Exception as exc:
        typer.echo(safe_error(exc), err=True)
        raise typer.Exit(1) from None
    show(result)
    if not result["ready"]:
        raise typer.Exit(1)


@app.command()
def validate(protocol: str | None = None):
    """Validate all configuration without credentials or network calls."""
    try:
        _, protocols = configuration(protocol)
        show(
            [
                {
                    "id": p.id,
                    "sources": len(p.sources),
                    "general_analysis": True,
                    "optional_focus_questions": len(p.analysis.focus),
                }
                for p in protocols
                if protocol is None or p.id == protocol
            ]
        )
    except Exception as exc:
        typer.echo(safe_error(exc), err=True)
        raise typer.Exit(1) from None


@app.command("init-db")
def init_db():
    """Apply migrations; do this before starting collection workers."""
    from alembic import command
    from alembic.config import Config

    try:
        settings = Settings()
        if not settings.database_url.get_secret_value():
            raise ValueError("DATABASE_URL is required")
        os.environ["DATABASE_URL"] = settings.database_url.get_secret_value()
        command.upgrade(Config("alembic.ini"), "head")
        show({"migrations": "applied"})
    except Exception as exc:
        typer.echo(safe_error(exc), err=True)
        raise typer.Exit(1) from None


@app.command("sync-config")
def sync_config():
    async def task():
        settings, protocols = configuration()
        db = Database(settings.database_url.get_secret_value())
        try:
            await db.sync(protocols)
            show({"protocols_synced": len(protocols)})
        finally:
            await db.close()

    run(task())


@app.command("storage-check")
def storage_check(verify_only: bool = False):
    """Write and verify diagnostic evidence, or read it again after a process restart."""

    async def task():
        settings = Settings()
        db = Database(settings.database_url.get_secret_value())
        try:
            async with asyncio.timeout(180):
                show(
                    await check_storage(
                        db, open_blobs(settings), storage_identity(settings), verify_only
                    )
                )
        finally:
            await db.close()

    run(task())


@app.command("analysis-preview")
def analysis_preview(
    protocol: str,
    run_id: str = typer.Option(..., help="Reuse the same ID to resume without another paid call"),
    send: bool = False,
    output: Path | None = None,
    resume_only: bool = typer.Option(False, help="Require a saved response; never call OpenAI"),
):
    """Make one paid sampled baseline review; optionally send its labeled report to Telegram."""

    async def task():
        settings, _ = configuration(protocol)
        db = Database(settings.database_url.get_secret_value())
        analyzer = None
        telegram = None
        try:
            blobs = open_blobs(settings)
            if send:
                telegram = Telegram(settings)
                await telegram.check()
            if not resume_only:
                analyzer = OpenAIAnalyzer(settings)
            report = await generate_preview(
                db, blobs, analyzer, settings, protocol, run_id, resume_only=resume_only
            )
            show({"report_id": report["id"], **report["result"]["_preview"]})
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(await blobs.get(report["blob_hash"]))
            if telegram:
                for part in ("summary", "document"):
                    await db.execute(
                        "INSERT INTO outbox(id,report_id,destination,part) VALUES(:id,:report,:destination,:part) ON CONFLICT DO NOTHING",
                        id=digest(f"{report['id']}:{telegram.destination}:{part}".encode()),
                        report=report["id"],
                        destination=telegram.destination,
                        part=part,
                    )
                for _ in range(2):
                    if not await deliver_one(db, blobs, telegram, report_id=report["id"]):
                        break
                states = await db.rows(
                    "SELECT id,part,status,last_error FROM outbox WHERE report_id=:report AND destination=:destination",
                    report=report["id"],
                    destination=telegram.destination,
                )
                show({"telegram": states})
                if len(states) != 2 or any(row["status"] != "SENT" for row in states):
                    raise ValueError(
                        "Test report saved; Telegram delivery incomplete. Inspect delivery status before retrying with the same run ID"
                    )
        finally:
            if analyzer:
                await analyzer.close()
            if telegram:
                await telegram.close()
            await db.close()

    run(task())


@app.command("analysis-receipt")
def analysis_receipt(run_id: str, output: Path):
    """Inspect an archived test and storage without invoking AI or sending Telegram messages."""

    async def task():
        settings = Settings()
        db = Database(settings.database_url.get_secret_value())
        try:
            report = await inspect_preview(db, open_blobs(settings), run_id)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(report, encoding="utf-8")
            show({"receipt": str(output), "new_model_calls": 0, "telegram_messages": 0})
        finally:
            await db.close()

    run(task())


async def collect_task(
    protocol: str | None, limit: int, baseline: bool = False, force: bool = False
):
    settings, protocols = configuration(protocol)
    db, blobs = Database(settings.database_url.get_secret_value()), open_blobs(settings)
    http = HTTP(settings, db)
    try:
        await db.sync(protocols)
        if force:
            await db.execute(
                "UPDATE sources s SET next_check_at=now() WHERE EXISTS(SELECT 1 FROM subscriptions sub WHERE sub.source_id=s.id AND sub.active AND sub.protocol_id=:protocol)",
                protocol=protocol,
            )
        counts = await run_due(db, blobs, http, limit, protocol, baseline)
        show({"checks": counts})
        if baseline:
            missing = await db.rows(
                "SELECT s.spec->>'url' AS url,s.last_error FROM sources s JOIN subscriptions sub ON sub.source_id=s.id WHERE sub.protocol_id=:protocol AND sub.active AND s.current_hash IS NULL",
                protocol=protocol,
            )
            show({"baseline_complete": not missing, "remaining_sources": missing})
            if missing:
                raise ValueError("Baseline is incomplete; inspect failures and rerun to resume")
        if counts.get("FAILED_TO_CHECK"):
            raise ValueError("Some checks failed; successful observations were preserved")
    finally:
        await http.close()
        await db.close()


@app.command()
def baseline(protocol: str, limit: int = typer.Option(5000, min=1, max=20000)):
    """Baseline one named protocol without resetting established versions or invoking AI."""
    run(collect_task(protocol, limit, baseline=True))


@app.command("run-due")
def run_due_command(limit: int = typer.Option(100, min=1, max=20000)):
    """Collect only due sources; this does not analyze or send notifications."""
    run(collect_task(None, limit))


@app.command()
def collect(protocol: str, limit: int = typer.Option(1000, min=1, max=20000)):
    """Check a protocol now; first observations are still baselines."""
    run(collect_task(protocol, limit, force=True))


async def analyze_task(
    settings: Settings, db: Database, limit: int, protocol_id: str | None = None
):
    await reconcile_jobs(db)
    await queue_clusters(db, settings, protocol_id, include_manual=protocol_id is not None)
    pending = await db.rows(
        "SELECT count(*) AS n FROM jobs j JOIN protocols p ON p.id=j.protocol_id WHERE j.status='PENDING' AND p.enabled AND (CAST(:protocol AS text) IS NULL OR j.protocol_id=:protocol)",
        protocol=protocol_id,
    )
    if not pending[0]["n"]:
        return {"jobs_processed": 0, "model_calls": 0}
    analyzer, blobs = OpenAIAnalyzer(settings), open_blobs(settings)
    processed, failed = 0, 0
    try:
        for _ in range(limit):
            job = await claim_job(db, protocol_id)
            if job is None:
                break
            try:
                await process_job(db, blobs, analyzer, job, settings)
                processed += 1
            except Exception as exc:
                failed += 1
                show({"analysis_job": job["id"], "error": safe_error(exc)})
        return {"jobs_processed": processed, "failed_attempts": failed}
    finally:
        await analyzer.close()


@app.command()
def analyze(
    limit: int = typer.Option(10, min=1, max=100), protocol: str | None = typer.Option(None)
):
    """Analyze archived changes; an explicit invocation may use OpenAI API credit."""

    async def task():
        settings, protocols = configuration(protocol)
        db = Database(settings.database_url.get_secret_value())
        try:
            await db.sync(protocols)
            result = await analyze_task(settings, db, limit, protocol)
            show(result)
            if result.get("failed_attempts"):
                raise ValueError("Analysis failures remain queued/visible; inspect status")
        finally:
            await db.close()

    run(task())


@app.command("retry-analysis")
def retry_analysis(job_id: str):
    """Requeue a failed analysis with its original input; does not invoke the model itself."""

    async def task():
        db = Database(Settings().database_url.get_secret_value())
        try:
            await retry_job(db, job_id)
            show({"analysis_job": job_id, "status": "PENDING"})
        finally:
            await db.close()

    run(task())


async def cycle_task(settings: Settings, db: Database, blobs) -> dict:
    cycle_id = uid()
    await db.execute("INSERT INTO monitor_cycles(id) VALUES(:id)", id=cycle_id)
    result: dict = {"cycle_id": cycle_id, "errors": {}}

    async def stage(name, operation):
        try:
            value = await operation()
            result[name] = value
            if isinstance(value, dict) and (
                value.get("FAILED_TO_CHECK") or value.get("failed_attempts")
            ):
                result["errors"][name] = "Failures remain visible; inspect the recorded details"
        except Exception as exc:
            result["errors"][name] = safe_error(exc)

    async def collect():
        http = HTTP(settings, db)
        try:
            return await run_due(db, blobs, http, 2000)
        finally:
            await http.close()

    async def deliver():
        telegram = Telegram(settings)
        try:
            count = 0
            for _ in range(40):
                if not await deliver_one(db, blobs, telegram):
                    break
                count += 1
            unresolved = await db.rows(
                "SELECT status,count(*) AS count FROM outbox WHERE status IN ('FAILED','UNKNOWN') OR (status='PENDING' AND last_error IS NOT NULL) GROUP BY status"
            )
            if unresolved:
                result["errors"]["delivery"] = canonical(unresolved)
            return {"attempts": count, "unresolved": unresolved}
        finally:
            await telegram.close()

    # Each stage runs even after another fails, so a collector error cannot suppress health delivery.
    if settings.collection_enabled:
        await stage("collection", collect)
    if settings.analysis_enabled:
        await stage("analysis", lambda: analyze_task(settings, db, 5))
    if settings.daily_digest_enabled:
        await stage("digest", lambda: queue_daily_digest(db, blobs, settings))
    if settings.notifications_enabled:
        await stage("delivery", deliver)
    unhealthy = await db.rows("""
      SELECT j.id,j.status FROM jobs j JOIN protocols p ON p.id=j.protocol_id
      WHERE p.enabled AND j.kind IN ('general','focus') AND j.status IN ('FAILED','BLOCKED')
    """)
    if unhealthy:
        result["errors"]["analysis_backlog"] = canonical(unhealthy)
    await db.execute(
        "UPDATE monitor_cycles SET completed_at=now(),status=:status,result=CAST(:result AS jsonb) WHERE id=:id",
        id=cycle_id,
        status="FAILED" if result["errors"] else "COMPLETE",
        result=canonical(result),
    )
    return result


@app.command()
def cycle():
    """Run one configured collection/analysis/digest/delivery cycle and persist its outcome."""

    async def task():
        settings, protocols = configuration()
        db = Database(settings.database_url.get_secret_value())
        try:
            await db.sync(protocols)
            result = await cycle_task(settings, db, open_blobs(settings))
            show(result)
            if result["errors"]:
                raise ValueError(
                    "Cycle completed with unresolved failures; inspect its stage results"
                )
        finally:
            await db.close()

    run(task())


@app.command("daily-digest")
def daily_digest():
    """Queue the next due UTC day's stored-report digest; no model call or direct send."""

    async def task():
        settings = Settings()
        db = Database(settings.database_url.get_secret_value())
        try:
            show(
                {
                    "digest_report_id": await queue_daily_digest(
                        db, open_blobs(settings), settings
                    ),
                    "new_model_calls": 0,
                }
            )
        finally:
            await db.close()

    run(task())


@app.command()
def status():
    """Show coverage, evidence, analysis backlog and Telegram delivery outcomes."""

    async def task():
        db = Database(Settings().database_url.get_secret_value())
        try:
            show(
                {
                    "sources": await db.rows(
                        "SELECT count(*) AS total,count(*) FILTER(WHERE current_hash IS NULL) AS awaiting_baseline,count(*) FILTER(WHERE failures>0) AS failing,count(*) FILTER(WHERE next_check_at<now()) AS due FROM sources WHERE EXISTS(SELECT 1 FROM subscriptions sub JOIN protocols p ON p.id=sub.protocol_id WHERE sub.source_id=sources.id AND sub.active AND p.enabled)"
                    ),
                    "events": await db.rows(
                        "SELECT baseline,count(*) FROM events GROUP BY baseline"
                    ),
                    "analysis": await db.rows("SELECT status,count(*) FROM jobs GROUP BY status"),
                    "delivery": await db.rows("SELECT status,count(*) FROM outbox GROUP BY status"),
                    "digest": await db.rows("SELECT next_day FROM daily_digest_state"),
                    "recent_cycles": await db.rows(
                        "SELECT id,started_at,completed_at,status,result FROM monitor_cycles ORDER BY started_at DESC LIMIT 5"
                    ),
                }
            )
        finally:
            await db.close()

    run(task())


@app.command()
def failures():
    async def task():
        db = Database(Settings().database_url.get_secret_value())
        try:
            show(
                {
                    "sources": await db.rows(
                        "SELECT spec->>'url' AS url,failures,last_error,last_success_at FROM sources WHERE failures>0 ORDER BY failures DESC"
                    ),
                    "analysis": await db.rows(
                        "SELECT id,status,last_error FROM jobs WHERE last_error IS NOT NULL"
                    ),
                    "telegram": await db.rows(
                        "SELECT id,status,last_error FROM outbox WHERE last_error IS NOT NULL"
                    ),
                }
            )
        finally:
            await db.close()

    run(task())


def telegram_command(method: str):
    async def task():
        telegram = Telegram(Settings())
        try:
            result = await getattr(telegram, method)()
            show(result)
            if method == "discover" and not result:
                raise ValueError(
                    "No setup marker found. Add the bot as channel admin, then post: protocol-intel setup"
                )
        finally:
            await telegram.close()

    run(task())


@app.command("telegram-discover")
def telegram_discover():
    """Find a channel ID from its exact 'protocol-intel setup' marker; sends nothing."""
    telegram_command("discover")


@app.command("telegram-check")
def telegram_check():
    """Verify bot identity, channel and Post Messages permission; sends nothing."""
    telegram_command("check")


@app.command("telegram-test")
def telegram_test():
    """Send one explicitly labeled connection-test message to the configured channel."""
    telegram_command("test")


@app.command()
def notify(limit: int = typer.Option(20, min=1, max=100)):
    """Deliver queued material reports when NOTIFICATIONS_ENABLED=true."""

    async def task():
        settings = Settings()
        if not settings.notifications_enabled:
            raise ValueError("Set NOTIFICATIONS_ENABLED=true to deliver queued reports")
        db, telegram = Database(settings.database_url.get_secret_value()), Telegram(settings)
        try:
            count = 0
            for _ in range(limit):
                if not await deliver_one(db, open_blobs(settings), telegram):
                    break
                count += 1
            show({"delivery_attempts": count})
        finally:
            await telegram.close()
            await db.close()

    run(task())


@app.command("resolve-delivery")
def resolve_delivery(
    delivery_id: str, outcome: Literal["sent", "retry"], message_id: int | None = None
):
    """After inspecting the channel, mark an uncertain send acknowledged or explicitly retry it."""

    async def task():
        db = Database(Settings().database_url.get_secret_value())
        try:
            if outcome == "sent" and message_id is None:
                raise ValueError("A confirmed Telegram message ID is required for outcome=sent")
            await db.execute(
                "UPDATE outbox SET status=:status,message_id=:message,next_attempt_at=now(),last_error=NULL WHERE id=:id AND status IN ('UNKNOWN','FAILED')",
                status="SENT" if outcome == "sent" else "PENDING",
                message=message_id,
                id=delivery_id,
            )
            show(
                await db.rows(
                    "SELECT id,status,message_id FROM outbox WHERE id=:id", id=delivery_id
                )
            )
        finally:
            await db.close()

    run(task())


@app.command()
def evidence(content_hash: str, output: Path):
    """Reconstruct exact archived evidence by its SHA-256 hash."""

    async def task():
        data = await open_blobs(Settings()).get(content_hash)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        show({"written": str(output), "bytes": len(data)})

    run(task())


@app.command()
def worker(role: Literal["all", "collector", "analyzer", "notifier", "digester"] = "all"):
    """Run independent role loops; enable each role explicitly in environment settings."""

    async def task():
        settings, protocols = configuration()
        db, blobs = Database(settings.database_url.get_secret_value()), open_blobs(settings)
        http = HTTP(settings, db)
        telegram = Telegram(settings) if settings.notifications_enabled else None

        async def loop(kind: str):
            while True:
                try:
                    if kind == "collector":
                        show({"collector": await run_due(db, blobs, http)})
                    elif kind == "analyzer":
                        show({"analyzer": await analyze_task(settings, db, 5)})
                    elif kind == "digester":
                        show({"digest": await queue_daily_digest(db, blobs, settings)})
                    elif telegram:
                        for _ in range(20):
                            if not await deliver_one(db, blobs, telegram):
                                break
                except Exception as exc:
                    show({"role": kind, "error": safe_error(exc)})
                await asyncio.sleep(settings.worker_sleep_seconds)

        try:
            await db.sync(protocols)
            enabled = [
                ("collector", settings.collection_enabled),
                ("analyzer", settings.analysis_enabled),
                ("notifier", settings.notifications_enabled),
                ("digester", settings.daily_digest_enabled),
            ]
            tasks = [kind for kind, flag in enabled if flag and role in {"all", kind}]
            if not tasks:
                raise ValueError(
                    "No roles enabled; configure COLLECTION_ENABLED, ANALYSIS_ENABLED, NOTIFICATIONS_ENABLED"
                )
            async with asyncio.TaskGroup() as group:
                for kind in tasks:
                    group.create_task(loop(kind))
        finally:
            await http.close()
            if telegram:
                await telegram.close()
            await db.close()

    run(task())
