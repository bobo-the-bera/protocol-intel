# Prepared release validation

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
