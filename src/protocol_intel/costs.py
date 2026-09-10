"""Separate measured resource usage from dated price estimates and scenarios."""

import asyncio
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from protocol_intel.blobs import LocalBlobs, S3Blobs
from protocol_intel.database import Database

PRICE_DATE = "2026-09-09"
MODEL_PRICE_URL = "https://developers.openai.com/api/docs/models/gpt-5.6-sol"


def model_cost(usage: dict) -> dict:
    model = usage.get("returned_model", "")
    rates = {"gpt-5.6-sol": (4.0, 20.0), "gpt-5.6-luna": (0.2, 1.2), "gpt-5.6-terra": (2.0, 12.0)}
    base_model = (
        "gpt-5.6-sol"
        if model == "gpt-5.6"
        else next((name for name in rates if model == name or model.startswith(name + "-20")), "")
    )
    if base_model not in rates or date.today() > date(2026, 11, 21):
        return {"available": False, "reason": "Model price needs verification", "usage": usage}
    if "input_tokens" not in usage or "output_tokens" not in usage:
        return {"available": False, "reason": "Provider did not return token usage", "usage": usage}
    inputs, outputs = int(usage["input_tokens"]), int(usage["output_tokens"])
    cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
    if not 0 <= cached <= inputs or outputs < 0:
        raise ValueError("Provider returned inconsistent token usage")
    input_rate, output_rate = rates[base_model]
    if inputs > 272000:
        input_rate, output_rate = input_rate * 2, output_rate * 1.5
    base = (
        (inputs - cached) * input_rate + cached * input_rate * 0.1 + outputs * output_rate
    ) / 1e6
    writes = (usage.get("input_tokens_details") or {}).get("cache_write_tokens")
    high = base + (inputs - cached) * input_rate * 0.25 / 1e6
    if writes is not None:
        if not 0 <= int(writes) <= inputs - cached:
            raise ValueError("Provider returned inconsistent cache-write usage")
        # A returned cache-write count removes the uncertainty in the token-rate calculation.
        base += int(writes) * input_rate * 0.25 / 1e6
        high = base
    return {
        "available": True,
        "usd_low": round(base, 6),
        "usd_high": round(high, 6),
        "price_checked": PRICE_DATE,
        "source": "https://developers.openai.com/api/docs/models/" + base_model,
        "note": "Estimate, not an invoice. Range allows for cache-write premiums; output tokens already include reasoning. Taxes and account discounts excluded.",
    }


async def archive_inventory(blobs: LocalBlobs | S3Blobs) -> dict[str, int]:
    def scan():
        objects: dict[str, int] = {}
        if isinstance(blobs, LocalBlobs):
            for path in blobs.root.glob("sha256/*/*"):
                if path.is_file():
                    objects[path.relative_to(blobs.root).as_posix()] = path.stat().st_size
        else:
            # Paginate every application-owned prefix; fail explicitly above this audit's bound.
            for prefix in ("sha256/", "diagnostics/"):
                pages = blobs.client.get_paginator("list_objects_v2")
                for page in pages.paginate(Bucket=blobs.bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        objects[obj["Key"]] = int(obj["Size"])
                    if len(objects) > 100000:
                        raise ValueError(
                            "Storage audit exceeds 100,000 objects; use a larger inventory audit"
                        )
        return objects

    return await asyncio.to_thread(scan)


async def resource_usage(db: Database, blobs: LocalBlobs | S3Blobs) -> dict:
    objects = await archive_inventory(blobs)
    by_hash = {Path(key).name: size for key, size in objects.items() if key.startswith("sha256/")}
    references = await db.rows("""
      SELECT sub.protocol_id,v.raw_hash AS hash FROM versions v JOIN subscriptions sub ON sub.source_id=v.source_id
      UNION SELECT sub.protocol_id,v.normalized_hash FROM versions v JOIN subscriptions sub ON sub.source_id=v.source_id
      UNION SELECT r.protocol_id,r.blob_hash FROM reports r
      UNION SELECT j.protocol_id,c.input_hash FROM analysis_chunks c JOIN jobs j ON j.id=c.job_id
      UNION SELECT j.protocol_id,j.payload->>'input_hash' FROM jobs j WHERE j.kind='baseline_test'
      UNION SELECT j.protocol_id,c.result->>'raw_response_hash' FROM analysis_chunks c JOIN jobs j ON j.id=c.job_id WHERE c.result ? 'raw_response_hash'
      UNION SELECT j.protocol_id,c.input_hash FROM model_calls c JOIN jobs j ON j.id=c.job_id
      UNION SELECT j.protocol_id,c.response_hash FROM model_calls c JOIN jobs j ON j.id=c.job_id WHERE c.response_hash IS NOT NULL
    """)
    owners: dict[str, set[str]] = defaultdict(set)
    for row in references:
        if row["hash"]:
            owners[row["hash"]].add(row["protocol_id"])
    missing = sorted(key for key in owners if key not in by_hash)
    if missing:
        raise ValueError(f"Storage audit found {len(missing)} referenced archive objects missing")
    protocols = await db.rows("""
      SELECT p.id,p.name,
        (SELECT count(*) FROM subscriptions sub WHERE sub.protocol_id=p.id AND sub.active) AS sources,
        (SELECT count(*) FROM subscriptions sub JOIN sources s ON s.id=sub.source_id WHERE sub.protocol_id=p.id AND sub.active AND s.current_hash IS NOT NULL) AS baselined_sources,
        (SELECT count(*) FROM subscriptions sub JOIN events e ON e.source_id=sub.source_id WHERE sub.protocol_id=p.id AND NOT e.baseline) AS change_events,
        ((SELECT count(*) FROM jobs j JOIN analysis_chunks c ON c.job_id=j.id WHERE j.protocol_id=p.id) + (SELECT count(*) FROM jobs j JOIN model_calls c ON c.job_id=j.id WHERE j.protocol_id=p.id AND c.status='RESPONDED')) AS recorded_model_responses
      FROM protocols p ORDER BY p.id
    """)
    for row in protocols:
        owned = {key for key, protocols in owners.items() if row["id"] in protocols}
        row["archive_referenced_bytes"] = sum(by_hash[key] for key in owned)
        row["archive_exclusive_bytes"] = sum(by_hash[key] for key in owned if len(owners[key]) == 1)
        row["archive_shared_bytes"] = (
            row["archive_referenced_bytes"] - row["archive_exclusive_bytes"]
        )
    database = (await db.rows("SELECT pg_database_size(current_database()) AS bytes"))[0]["bytes"]
    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "protocols": protocols,
        "archive_bytes": sum(objects.values()),
        "archive_objects": len(objects),
        "database_physical_bytes": database,
        "archive_unattributed_bytes": sum(objects.values()) - sum(by_hash[key] for key in owners),
        "note": "Measured before this report is uploaded. Payload bytes in sha256/ and diagnostics/. Shared objects count once globally; per-protocol referenced totals overlap. Database size includes shared tables/indexes/catalogs; Neon billed compute, account allowances, backups and other bucket prefixes are not measured.",
    }


def cost_text(cost: dict) -> str:
    if not cost.get("available"):
        return "API dollar estimate unavailable; consult the returned tokens and OpenAI usage dashboard."
    return f"Estimated API charge: ${cost['usd_low']:.4f}–${cost['usd_high']:.4f} USD (not an invoice)."


def usage_markdown(calls: list[dict]) -> str:
    lines = [
        "## Measured model usage",
        "",
        "| Stage | Model | Input tokens | Output tokens | Estimated USD |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for call in calls:
        usage, cost = call["usage"], call["cost"]
        amount = (
            f"${cost['usd_low']:.5f}–${cost['usd_high']:.5f}"
            if cost.get("available")
            else "unavailable"
        )
        lines.append(
            f"| {call['stage']} | {usage.get('returned_model', 'unknown')} | {usage.get('input_tokens', 'unknown')} | {usage.get('output_tokens', 'unknown')} | {amount} |"
        )
    if all(c["cost"].get("available") for c in calls):
        low = sum(c["cost"]["usd_low"] for c in calls)
        high = sum(c["cost"]["usd_high"] for c in calls)
        lines.extend(
            [
                "",
                f"Total estimated API charge: ${low:.5f}–${high:.5f}. Output tokens include reasoning. This is not an invoice.",
            ]
        )
    else:
        lines.extend(
            ["", "Some calls lack a verified dollar estimate; total cost is not reported as zero."]
        )
    return "\n".join(lines)


def resource_markdown(metrics: dict, cost: dict) -> str:
    lines = [
        "## Measured storage",
        "",
        "| Protocol | Baselined sources | Referenced archive MiB | Exclusive archive MiB | Shared archive MiB |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for p in metrics["protocols"]:
        lines.append(
            f"| {p['id']} | {p['baselined_sources']}/{p['sources']} | {p['archive_referenced_bytes'] / 2**20:.3f} | {p['archive_exclusive_bytes'] / 2**20:.3f} | {p['archive_shared_bytes'] / 2**20:.3f} |"
        )
    lines.extend(
        [
            "",
            f"Application archive total: {metrics['archive_bytes'] / 2**20:.3f} MiB in {metrics['archive_objects']} objects.",
            f"Whole database physical size: {metrics['database_physical_bytes'] / 2**20:.3f} MiB.",
            metrics["note"],
            "",
            "## Adding another protocol: scenarios, not a forecast",
            "",
            "There is no fixed per-protocol software fee. Source count, change frequency, request size, analysis chunks and storage growth determine incremental usage.",
        ]
    )
    if cost.get("available"):
        lines.extend(
            [
                "",
                "| Comparable model calls per month | Estimated API USD/month |",
                "| ---: | ---: |",
            ]
        )
        for calls in (1, 10, 100, 1000):
            lines.append(
                f"| {calls} | ${calls * cost['usd_low']:.3f}–${calls * cost['usd_high']:.3f} |"
            )
    lines.extend(
        [
            "",
            "A comparable call means this test's input/output token workload. Real change clusters may need multiple chunk calls, synthesis and optional focus calls; the sampled baseline test is not a measured average production alert. An unchanged check normally needs zero model calls.",
            "",
            "R2 Standard: $0.015/GB-month, $4.50/million Class A operations, $0.36/million Class B operations. Account-wide free allowances are 10 GB-month, 1M A and 10M B operations/month; billable units round up. Current archive size alone cannot predict object growth or the bill, and polling requests to source websites are not all R2 operations.",
            "Neon: database compute is shared. Launch reference rates are $0.106/CU-hour and $0.35/GB-month; the Free plan has usage limits. This report cannot read your Neon billing/compute history, so it does not assign a fabricated database cost per protocol.",
            "GitHub standard hosted runner minutes are free while this repository remains public; private repositories, larger runners and artifact/cache allowances differ.",
            "",
            f"Rates checked {PRICE_DATE}; verify provider dashboards for actual bills and current prices.",
            f"Sources: {MODEL_PRICE_URL} ; https://developers.cloudflare.com/r2/pricing/ ; https://neon.com/pricing ; https://docs.github.com/en/billing/concepts/product-billing/github-actions",
        ]
    )
    return "\n".join(lines) + "\n"
