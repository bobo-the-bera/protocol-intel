# Prepared release validation

## First live model response — 9 September 2026

[Analysis test 34414521438](https://github.com/bobo-the-bera/protocol-intel/actions/runs/34414521438) received a completed GPT-5.6 Sol response: 7,852 input tokens (7,849 cache-write tokens, zero cache hits), 4,469 output tokens including 2,509 reasoning tokens. At the recorded standard rates its token estimate is $0.128637, not a verified invoice. Raw response and usage were saved before validation.

The run failed evidence-ID validation and sent no analysis report to Telegram. The exact mismatch cannot be inferred from the error alone. The Analysis receipt workflow exposes the saved response and supplied evidence without another model call; follow-up must inspect that data before repairing or replacing findings. New preview schemas constrain citations to the allowed IDs. This is an unresolved live output-validation failure, not successful end-to-end delivery.

The production daily digest remains unimplemented (SPEC-02). Monitor defines an hourly `17 * * * *` schedule gated by `MONITOR_ENABLED`; the scheduled run at 22:28 UTC was skipped. Analysis and notification flags default off. At the time of this run immediate alerts included MEDIUM findings; the cost-routing change raises the default immediate threshold to HIGH while preserving MEDIUM reports. Digest implementation and its acceptance tests remain required before enabling the requested daily-plus-urgent behavior.

## Successful live reference baselines — 9 September 2026

[First run 34411588023](https://github.com/bobo-the-bera/protocol-intel/actions/runs/34411588023) passed on main after the decompression fix. NEAR recorded 70 BASELINE observations and HyperLend recorded 66; both reported `baseline_complete: true`. These counts include configured source kinds such as sitemaps, not exclusively documentation pages. Archive conditional-write/readback and fresh-process verification passed again. Baselines made no model calls and requested no Telegram alerts.

This completes the current configured snapshots, not the complete official repository/frontend footprint required by PROD-06. The backup/restore drill in PROD-05 also remains open. The earlier failed attempt below is retained as history.

The [Analysis test](ANALYSIS_TEST.md) provides a separate, explicitly requested paid preview, usage receipt, storage audit and Telegram summary/document check. Its live model and document-delivery result must be recorded after execution; mock tests are not evidence of live integration.

## Live storage verification — 9 September 2026

[First run 34410049030, attempt 2](https://github.com/bobo-the-bera/protocol-intel/actions/runs/34410049030/attempts/2) applied the production database migrations and passed both archive write/readback and readback from a fresh process. The S3-compatible backend rejected a conditional overwrite and preserved the original bytes. Probe `826bc179-c7ee-454b-9ead-abef176024aa` is recorded in the database and archive. This is verified storage connectivity and persistence, not a backup/restore drill; PROD-05 remains open.

The same attempt failed to baseline all four starting URLs with a decompression error. The client had already decompressed the response, but the text helper passed that decoded body back through the compression decoder while looking up its charset. The correction reads charset metadata separately, retaining strict text decoding and decompressed-size limits. New regression tests reproduced the exact failure before the correction. A fresh First run from `main` is needed to verify the repaired live baselines; rerunning an older workflow run retains its old code revision.

The preceding setup implementation passed [CI run 34409240781](https://github.com/bobo-the-bera/protocol-intel/actions/runs/34409240781) on commit `570a4c032cabddaabfca83c16c3fca494d1b16b1`: **71 tests passed with zero skips**, including 19 Postgres tests, plus container build and validation.

## Published CI verification — 9 September 2026

[CI run 34368778816](https://github.com/bobo-the-bera/protocol-intel/actions/runs/34368778816) passed on commit `36de58d3e7abff11e6b6a75f2d027c22ad6f60cf`: **63 tests passed with zero skips**, including all 16 Postgres integration tests. The Postgres 18 fixture applies migrations twice. Ruff, formatting, mypy, configuration validation, and the runtime container build and validation all passed. This verifies PROD-01; nine first-production requirements remain open.

[PR #1](https://github.com/bobo-the-bera/protocol-intel/pull/1) was merged. The Telegram setup workflow is now available on `main`. The owner confirmed successful delivery of the manual Telegram setup test. This verifies the configured bot can post to the intended channel. Full report/document delivery, concurrency and ambiguous-send recovery remain unverified, so PROD-09 stays open.

The snapshots below describe earlier checks and blockers, not the current publication state.

**Superseded by the 9 September [prepublication review](PREPUBLICATION_REVIEW.md): 47 local tests pass, 16 Postgres tests remain skipped, and production release is blocked.** GitHub installation now includes the repository; no remote writes were attempted during the review. The following table preserves the earlier snapshot rather than presenting it as current acceptance evidence.

Snapshot prepared on 2026-09-08. This records what was actually checked before publication was blocked.

| Check | Result |
| --- | --- |
| Offline unit tests | 31 passed |
| Postgres integration tests | 12 present, skipped locally because no disposable Postgres service was available |
| Ruff lint and formatting | Passed |
| Mypy | Passed for 11 source files |
| Starter protocol configuration | Both configurations passed validation without a mandatory focus file |
| Workflow definitions | YAML structure loaded and embedded shell scripts passed Bash syntax checks |
| CLI registration/help | Passed |
| Public endpoint reachability | NEAR Intents and HyperLend sitemaps plus both configured homepages returned HTTP 200 through the workspace HTTP client |
| Full collector live baseline | Not run; the collector's direct DNS checks encountered the workspace's temporary name-resolution failure |
| Docker image and real Postgres CI | Not run; awaiting repository publication so GitHub Actions can execute the supplied checks |
| OpenAI, S3/R2, and Telegram live integration | Not run; owner-controlled credentials are not configured |
| GitHub publication | Blocked: first file write returned HTTP 403, `Resource not accessible by integration`; no remote changes were made |

The integration tests cover source transitions, shared subscriptions, concurrency, expired-worker fencing, failure recovery, general/focus independence, abandoned analysis recovery, evidence-ID validation, and Telegram outbox behavior. These tests must actually pass against Postgres before deployment acceptance. Local unit tests mock OpenAI and Telegram and do not spend API credit or send channel messages.

See [PUBLISHING.md](PUBLISHING.md) for the connection prerequisite, then [DEPLOYMENT.md](DEPLOYMENT.md) for the production pilot acceptance steps.
