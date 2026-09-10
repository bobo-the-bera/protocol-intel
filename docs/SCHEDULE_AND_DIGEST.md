# Scheduled monitoring and daily Telegram reporting

The owner requested this scoped docs/homepage pilot on 10 September 2026. Implementation is tested in PR #7; automatic approval review requires explicit activation approval before its merge. The existing eight secrets are sufficient. The recovered NEAR baseline test successfully delivered both summary and document in [run 34471081681](https://github.com/bobo-the-bera/protocol-intel/actions/runs/34471081681); it reused the original response and incurred no new AI call.

## Timing and controls

| Work | Default behavior |
| --- | --- |
| Scheduler | Minutes 7, 22, 37 and 52 of every hour, UTC |
| Collection | Only sources due in Postgres; preserves configured/adaptive per-source cadence |
| Broad analysis | Ten-minute change-cluster window; Luna first pass, Sol escalation and routine audit |
| Immediate alerts | HIGH/CRITICAL findings, after analysis succeeds |
| Daily digest | Previous UTC calendar day, due at 08:00 UTC; sent by the next actual cycle |
| Digest model cost | Zero additional model calls; uses stored reports and operational records |
| Outage recovery | One owed daily digest per subsequent cycle, in chronological order |

The trigger frequency is not a promise to refetch every page every 15 minutes. Current homepages start at one hour and docs/sitemap schedules follow their configured or discovered cadence; stable unpinned pages back off. Alert latency includes source polling, the ten-minute clustering window, scheduler delay, analysis and delivery. [GitHub documents possible delays or dropped scheduled runs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). An always-running worker is needed for stricter timing. The configured GitHub history and frontend bundle coverage requirements remain separate unfinished work; this pilot monitors the current docs/homepage inventory.

The Monitor workflow defaults `MONITOR_ENABLED`, `COLLECTION_ENABLED`, `ANALYSIS_ENABLED`, `NOTIFICATIONS_ENABLED` and `DAILY_DIGEST_ENABLED` on. An explicit repository Variable of `false` overrides the corresponding default. `MONITOR_ENABLED=false` pauses scheduled jobs, while manual status/recovery remains available. It does not cancel an active job. Local `.env` defaults remain off; enable `DAILY_DIGEST_ENABLED=true` for the daemon's independent digester role. `DAILY_DIGEST_HOUR_UTC` changes the due hour without changing UTC day boundaries.

## Digest semantics

Each day uses the half-open interval `[00:00 UTC, next 00:00 UTC)`. Report publication time selects completed general/focus analyses, so analysis of older events completed late appears on its completion day. Baseline preview tests and prior digest reports are excluded. MEDIUM/HIGH/CRITICAL findings are included; exact duplicates share one entry with all report references retained. Previously acknowledged alerts are marked as recaps. The Telegram summary shows at most five findings, while the attached Markdown report preserves all findings and operational details.

The digest also includes archived change counts, recorded checks, failed checks even if a source later recovered, current missing baselines/failures/overdue sources, unfinished or failed analysis, unassigned events, unresolved deliveries, cycle history and recorded tiered model usage. Overdue means more than 30 minutes beyond a source's own next-check time. Current health is timestamped separately from the prior-day reporting window; it is not a reconstruction of historical coverage. Pending jobs and unassigned events are distinct units, and coverage flags can overlap.

An empty findings list is not a claim of complete, unchanged coverage. Days without recorded cycles explicitly say monitoring continuity is unestablished. Unknown paid outcomes and legacy model calls are disclosed instead of being priced as zero. The daily usage table covers saved tiered response receipts completed during the window, including failed validation; it excludes baseline test charges and is not a provider invoice. API responses already archived by an earlier run are not purchased again merely to build a digest.

## Persistence and failure handling

`daily_digest_state.next_day` starts at the previous UTC day on first initialization. It is persisted before report construction, so even a first upload failure across midnight retains the owed day. PostgreSQL serializes digest workers. Report upload, report record, two outbox parts and cursor advancement form one publication transaction: a failed publication leaves the day retryable. Earlier days before initialization are not silently fabricated as monitored history.

The summary and document use stable delivery identities. Definite transport/API failures retry with backoff. A submission without a reliable acknowledgement becomes UNKNOWN and requires channel inspection before `resolve-delivery`; automatic retry cannot guarantee exactly-once Telegram delivery. Active delivery is bounded to four minutes, below the five-minute abandoned-send threshold. Original analysis reports must still be readable from verified blob storage before digest publication.

`monitor_cycles` records start/completion and each stage result. Collection, analysis, digest generation and queued delivery are independently attempted; one failed stage cannot silently suppress the others. Failed stages, terminal analysis failures and unresolved delivery errors keep the cycle red. Abrupt process termination leaves an unfinished cycle receipt visible. A complete platform outage cannot send its own notification; GitHub failure notifications and subsequent catch-up reports are the available indicators in this deployment.

## Operator commands

```bash
uv run protocol-intel cycle
uv run protocol-intel daily-digest
uv run protocol-intel notify
uv run protocol-intel status
uv run protocol-intel failures
```

`daily-digest` only queues the next due day and makes no model call or direct Telegram send. `cycle` runs enabled roles and sends queued work when notifications are enabled. Run **Actions → Monitor → Run workflow → main → cycle** for an immediate check; a cycle covers all enabled protocols. Repeated runs resume stored work. Do not rebaseline established sources or rerun the paid Analysis test to activate scheduling.

CI run 34504177741 passed 126 tests with zero skips on implementation commit 6e0e2331e3131b08cc4f5d39666dda4f5791abb1. The first scheduled cycle and live daily digest must be recorded in VALIDATION.md after execution. Database/CI tests establish retry and boundary behavior; they do not themselves prove live scheduling or channel delivery.
