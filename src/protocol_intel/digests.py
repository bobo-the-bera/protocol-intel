"""Daily reports reuse stored findings; a durable cursor prevents missed days and duplicates."""

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import text

from protocol_intel.blobs import BlobStore
from protocol_intel.config import Settings, canonical, digest
from protocol_intel.costs import model_cost, usage_markdown
from protocol_intel.database import Database


def window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), UTC)
    return start, start + timedelta(days=1)


async def snapshot(conn, start: datetime, end: datetime, now: datetime) -> dict:
    async def rows(sql: str) -> list[dict]:
        result = await conn.execute(text(sql), {"start": start, "end": end, "now": now})
        return [
            {
                key: value.isoformat() if isinstance(value, (datetime, date)) else value
                for key, value in row.items()
            }
            for row in result.mappings()
        ]

    # Completion-time windows include late analysis on the day it becomes available.
    reports = await rows("""
      SELECT r.id,r.protocol_id,r.result,r.blob_hash,r.created_at,j.kind,
        EXISTS(SELECT 1 FROM outbox o WHERE o.report_id=r.id AND o.part='summary'
          AND o.status='SENT') AS alerted
      FROM reports r JOIN jobs j ON j.id=r.job_id
      WHERE r.created_at>=:start AND r.created_at<:end AND j.kind IN ('general','focus')
      ORDER BY r.created_at,r.id
    """)
    events = await rows("""
      SELECT p.id,count(*) AS changes FROM protocols p
      JOIN subscriptions sub ON sub.protocol_id=p.id AND sub.active
      JOIN events e ON e.source_id=sub.source_id AND e.sequence>sub.baseline_sequence
      WHERE p.enabled AND NOT e.baseline AND e.created_at>=:start AND e.created_at<:end
      GROUP BY p.id ORDER BY p.id
    """)
    coverage = await rows("""
      SELECT p.id,count(s.id) AS sources,
        count(s.id) FILTER(WHERE s.current_hash IS NULL) AS missing_baselines,
        count(s.id) FILTER(WHERE s.failures>0) AS failing,
        count(s.id) FILTER(WHERE s.next_check_at<:now-interval '30 minutes') AS overdue
      FROM protocols p LEFT JOIN subscriptions sub ON sub.protocol_id=p.id AND sub.active
      LEFT JOIN sources s ON s.id=sub.source_id WHERE p.enabled GROUP BY p.id ORDER BY p.id
    """)
    failures = await rows("""
      SELECT p.id AS protocol,s.spec->>'url' AS url,s.failures,s.last_error,
        s.last_success_at,s.current_hash IS NULL AS missing_baseline,
        s.next_check_at<:now-interval '30 minutes' AS overdue
      FROM protocols p JOIN subscriptions sub ON sub.protocol_id=p.id AND sub.active
      JOIN sources s ON s.id=sub.source_id
      WHERE p.enabled AND (s.failures>0 OR s.current_hash IS NULL
        OR s.next_check_at<:now-interval '30 minutes') ORDER BY p.id,s.id
    """)
    checks = await rows("""
      SELECT outcome,count(*) AS count FROM checks
      WHERE checked_at>=:start AND checked_at<:end GROUP BY outcome ORDER BY outcome
    """)
    failed_checks = await rows("""
      SELECT c.source_id,s.spec->>'url' AS url,count(*) AS count,max(c.checked_at) AS last_failure
      FROM checks c JOIN sources s ON s.id=c.source_id
      WHERE c.checked_at>=:start AND c.checked_at<:end AND c.outcome='FAILED_TO_CHECK'
      GROUP BY c.source_id,s.spec ORDER BY c.source_id
    """)
    backlog = await rows("""
      SELECT j.id,j.protocol_id,j.kind,j.status,j.last_error FROM jobs j
      JOIN protocols p ON p.id=j.protocol_id
      WHERE p.enabled AND j.kind IN ('general','focus') AND j.status<>'DONE'
      ORDER BY j.created_at,j.id
    """)
    unqueued = await rows("""
      SELECT p.id,count(*) AS count FROM protocols p
      JOIN subscriptions sub ON sub.protocol_id=p.id AND sub.active
      JOIN events e ON e.source_id=sub.source_id AND e.sequence>sub.baseline_sequence
      WHERE p.enabled AND NOT e.baseline AND NOT EXISTS(SELECT 1 FROM job_events je
        WHERE je.protocol_id=p.id AND je.event_id=e.id) GROUP BY p.id ORDER BY p.id
    """)
    deliveries = await rows("""
      SELECT id,report_id,part,status,last_error FROM outbox WHERE status<>'SENT'
      ORDER BY next_attempt_at,id
    """)
    cycles = await rows("""
      SELECT id,started_at,completed_at,status,result FROM monitor_cycles
      WHERE started_at>=:start AND started_at<:end ORDER BY started_at,id
    """)
    latest_cycle = await rows("""
      SELECT started_at,completed_at,status FROM monitor_cycles
      WHERE completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1
    """)
    calls = await rows("""
      SELECT c.job_id,c.call_key,c.usage FROM model_calls c JOIN jobs j ON j.id=c.job_id
      WHERE c.completed_at>=:start AND c.completed_at<:end AND j.kind IN ('general','focus')
      ORDER BY c.completed_at,c.job_id,c.call_key
    """)
    unknown_calls = await rows("""
      SELECT c.job_id,c.call_key,c.created_at FROM model_calls c JOIN jobs j ON j.id=c.job_id
      WHERE c.status='CALLING' AND j.kind IN ('general','focus') ORDER BY c.created_at
    """)
    legacy = await rows("""
      SELECT j.id FROM jobs j WHERE j.kind IN ('general','focus')
        AND j.payload->>'analysis_mode' IS DISTINCT FROM 'tiered'
        AND j.created_at<:end AND (j.status<>'DONE' OR EXISTS(SELECT 1 FROM reports r
          WHERE r.job_id=j.id AND r.created_at>=:start AND r.created_at<:end))
    """)
    return {
        "day": start.date().isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "generated_at": now.isoformat(),
        "reports": reports,
        "events": events,
        "coverage": coverage,
        "failures": failures,
        "checks": checks,
        "failed_checks": failed_checks,
        "backlog": backlog,
        "unqueued": unqueued,
        "deliveries": deliveries,
        "cycles": cycles,
        "latest_cycle": latest_cycle,
        "calls": calls,
        "unknown_calls": unknown_calls,
        "legacy_usage_unavailable": legacy,
    }


def findings(data: dict) -> list[dict]:
    # Exact duplicates across general/focus reports share one entry with all provenance retained.
    unique: dict[str, dict] = {}
    for report in data["reports"]:
        for finding in report["result"]["findings"]:
            if finding["importance"] == "LOW":
                continue
            key = canonical(
                [
                    report["protocol_id"],
                    finding["title"],
                    finding["observed_change"],
                    finding["significance"],
                    finding["uncertainty"],
                    sorted(set(finding["evidence"])),
                ]
            )
            item = unique.setdefault(
                key,
                {
                    "protocol": report["protocol_id"],
                    "finding": finding,
                    "reports": [],
                    "alerted": False,
                },
            )
            item["reports"].append(report["id"])
            item["alerted"] |= report["alerted"]
            if PRIORITY[finding["importance"]] > PRIORITY[item["finding"]["importance"]]:
                item["finding"] = finding
    return sorted(
        unique.values(),
        key=lambda item: (
            -PRIORITY[item["finding"]["importance"]],
            item["protocol"],
            item["finding"]["title"],
        ),
    )


PRIORITY = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def render(data: dict) -> tuple[str, str]:
    entries = findings(data)
    gaps = sum(p["failing"] + p["missing_baselines"] + p["overdue"] for p in data["coverage"])
    pending = len(data["backlog"]) + sum(p["count"] for p in data["unqueued"])
    failed = sum(c["count"] for c in data["failed_checks"])
    header = [
        f"DAILY DIGEST — {data['day']} UTC",
        f"{len(entries)} notable findings; {len(data['reports'])} completed analyses; "
        f"{sum(e['changes'] for e in data['events'])} protocol change events.",
        f"Health now: {gaps} coverage flags, {pending} unfinished jobs/unassigned events, "
        f"{len(data['deliveries'])} unsent delivery parts. Failed checks during day: {failed}.",
        "Daily recap from stored reports; no AI call for this digest.",
        f"Recorded cycles during day: {len(data['cycles'])}; "
        f"failed: {sum(c['status'] == 'FAILED' for c in data['cycles'])}; "
        f"without completion: {sum(c['completed_at'] is None for c in data['cycles'])}.",
    ]
    if not data["cycles"]:
        header.append(
            "No scheduled-cycle receipts for this day; continuous monitoring is not established for that window."
        )
    if not entries:
        header.append(
            "No material findings were recorded. This does not establish no changes when coverage or analysis is incomplete."
        )
    summary = header + [""]
    for item in entries[:5]:
        finding = item["finding"]
        label = "already alerted" if item["alerted"] else "not previously acknowledged as an alert"
        summary.append(
            f"[{finding['importance']}] {item['protocol']}: {finding['title'][:200]} ({label})"
        )
    summary.append(
        "Full findings, evidence references, coverage and costs are in the attached report."
    )
    if len(entries) > 5:
        summary.append(
            f"Showing 5 of {len(entries)} findings here; all are retained in the attachment."
        )
    lines = [
        f"# {header[0]}",
        "",
        *header[1:],
        "",
        f"Window: {data['window_start']} ≤ report completion < {data['window_end']}.",
        f"Health snapshot captured at {data['generated_at']}; it is current health, not reconstructed historical coverage.",
        "Late analysis appears in the next digest after completion. Already-alerted findings are labeled as recaps.",
        "",
    ]
    for item in entries:
        finding = item["finding"]
        lines.extend(
            [
                f"## [{finding['importance']}] {item['protocol']} — {finding['title']}",
                "",
                "Observed: " + finding["observed_change"],
                "",
                "Interpretation: " + finding["significance"],
                "",
                "Uncertainty: " + finding["uncertainty"],
                "",
                "Evidence event IDs: " + ", ".join(finding["evidence"]),
                "Reports: " + ", ".join(item["reports"]),
                "Previously alerted: " + str(item["alerted"]),
                "",
            ]
        )
    calls = [
        {"stage": c["call_key"], "usage": c["usage"], "cost": model_cost(c["usage"])}
        for c in data["calls"]
    ]
    lines.extend(
        [
            usage_markdown(calls),
            "",
            f"Unresolved API submissions: {len(data['unknown_calls'])}; legacy jobs without complete daily usage: {len(data['legacy_usage_unavailable'])}.",
            "Costs above cover recorded tiered responses completed in this window, including failed validations. Unknown submissions and legacy calls prevent treating this as a complete invoice. Test previews are excluded.",
            "",
            "## Coverage, failures, cycle history and report provenance",
            "",
            "Source events are counted per subscribed protocol. Coverage flags can overlap. Overdue means over 30 minutes past next scheduled source check.",
            "",
        ]
    )
    # Preserve full diagnostic details and findings without expanding the Telegram summary indefinitely.
    detail = canonical(data)
    import re

    fence = "`" * max(3, 1 + max((len(m) for m in re.findall(r"`+", detail)), default=0))
    lines.extend([fence + "json", detail, fence, ""])
    return "\n".join(summary), "\n".join(lines)


async def queue_daily_digest(
    db: Database, blobs: BlobStore, settings: Settings, now: datetime | None = None
) -> str | None:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Digest clock must be timezone-aware")
    now = now.astimezone(UTC)
    if not settings.telegram_chat_id:
        raise ValueError("Daily digest requires a Telegram destination")
    # Persist the first owed day even if its initial report upload fails across midnight.
    await db.execute(
        "INSERT INTO daily_digest_state(singleton,next_day) VALUES(true,:day) ON CONFLICT DO NOTHING",
        day=now.date() - timedelta(days=1),
    )
    async with db.engine.begin() as conn:
        # Only one worker owns cursor advancement; upload/commit failure leaves that day retryable.
        locked = (
            await conn.execute(text("SELECT pg_try_advisory_xact_lock(617429105)"))
        ).scalar_one()
        if not locked:
            return None
        day = (
            await conn.execute(
                text("SELECT next_day FROM daily_digest_state WHERE singleton FOR UPDATE")
            )
        ).scalar_one()
        start, end = window(day)
        if now < end + timedelta(hours=settings.daily_digest_hour_utc):
            return None
        data = await snapshot(conn, start, end, now)
        summary, document = render(data)
        # Read original report blobs before publication so missing evidence becomes an explicit failure.
        for report in data["reports"]:
            await blobs.get(report["blob_hash"])
        report_hash = await blobs.put(document.encode())
        report_id = "daily-digest-" + day.isoformat()
        await conn.execute(
            text(
                "INSERT INTO reports(id,material,result,blob_hash) VALUES(:id,false,CAST(:result AS jsonb),:hash)"
            ),
            {
                "id": report_id,
                "result": canonical(
                    {
                        "findings": [],
                        "nonmaterial_summary": "",
                        "_digest": {
                            "day": day.isoformat(),
                            "summary": summary,
                            "window_start": start.isoformat(),
                            "window_end": end.isoformat(),
                        },
                    }
                ),
                "hash": report_hash,
            },
        )
        for part in ("summary", "document"):
            await conn.execute(
                text(
                    "INSERT INTO outbox(id,report_id,destination,part) VALUES(:id,:report,:destination,:part)"
                ),
                {
                    "id": digest(f"{report_id}:{settings.telegram_chat_id}:{part}".encode()),
                    "report": report_id,
                    "destination": settings.telegram_chat_id,
                    "part": part,
                },
            )
        await conn.execute(
            text("UPDATE daily_digest_state SET next_day=:day WHERE singleton"),
            {"day": day + timedelta(days=1)},
        )
        return report_id
