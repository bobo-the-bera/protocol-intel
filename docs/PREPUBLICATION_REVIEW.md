# Prepublication review — 9 September 2026

## Decision

**The architecture is viable. The current implementation does not yet meet the specification's first-production acceptance criteria. Production release is blocked.**

The earlier handoff described a runnable documentation pilot and moved several capabilities into later work. That was too generous a readiness assessment: the owner did not approve replacing the specified system with a docs-only product. In particular, specification section 35 explicitly requires GitHub HEAD monitoring and every intervening commit. Missing that collector is a release blocker, not a completed task with a caveat.

This review makes targeted repairs to the existing code, retains unfinished requirements as open work, and pauses publication as requested. The GitHub installation now exists and includes `bobo-the-bera/protocol-intel`. No remote mutation was attempted during this review, so write access has not yet been exercised. The remote remains empty.

## Architecture worth keeping

- **Postgres plus immutable content-addressed evidence.** This is the appropriate separation for historical evidence, current source state, leases and queued work. Git should remain the code store, not the archive.
- **Archive before analysis.** Collection preserves observed changes before a model decides importance. Baselines, unchanged observations, failures and stale completions have separate outcomes.
- **Independent broad analysis.** The general request excludes optional focus questions. Collection does not read them. Prior general context is sourced from general reports. This directly addresses the requirement to find unexpected developments without an old thesis narrowing collection.
- **Separate work and delivery state.** An analysis failure should not erase evidence, and a Telegram failure should not erase the report. Those responsibilities belong in separate durable records.
- **One deployable application with role loops.** Separate collector/analyzer/notifier roles are useful; separate microservices are not yet justified. Actual scale depends on measured scheduling, storage and database behavior, not the existence of async code.

The important boundary: a broad prompt can inspect only evidence that reaches it. Missing repositories and frontend bodies leave blind spots no reasoning model can repair.

## Defects repaired in this review

| ID | Defect and practical consequence | Change and verification |
| --- | --- | --- |
| FIX-01 | Markdown discovery accepted any same-host `.md` link. A link to a different document could replace the page actually being monitored. | Require an equivalent page path/query or an explicit Markdown alternate. New unrelated-page and alternate tests pass. |
| FIX-02 | A stored Markdown endpoint returning 404/503 or a transport failure could stop canonical-page monitoring indefinitely. | Retry through the canonical page without carrying the Markdown validators. Regression cases pass. |
| FIX-03 | HTML table cells were concatenated: `[a, bc]` and `[ab, c]` could both normalize to `abc`, hiding a meaningful change. | Preserve cell delimiters. The collision regression passes. |
| FIX-04 | Extraction version was recorded in metadata but omitted from source identity. A later normalizer change could look like a protocol change against an incompatible baseline. | Include an explicit extraction version in identity; version-change regression passes. Database upgrade/baseline behavior still requires integration verification. |
| FIX-05 | HTTP read timeouts did not bound the entire operation. Slow-drip content, DNS and permit waits could hold work beyond the origin permit's lifetime. | Add a total fetch deadline below the two-minute permit lease and cap Retry-After. Deadline and recovery tests pass; shared permit behavior still requires Postgres verification. |
| FIX-06 | A queued job froze a prompt version label but used the current global prompt on retry. Only the user input was archived. | Freeze the system prompt, response schema and output budget, and archive the complete request contract with model and effort. Prompt drift regression passes; persisted request test is pending Postgres. |
| FIX-07 | A chunk could cite an event from its job that was not supplied in that chunk. | Validate against the IDs actually supplied to each chunk or synthesis call. The persistence path remains unverified until integration tests run. |
| FIX-08 | An exhausted worker could fail without a diagnostic error; its focus child could remain pending forever. Failed jobs also lacked a guarded operator retry route. | Reconcile expired workers, mark dependent focus work BLOCKED, and provide `retry-analysis` preserving original input and chunks. New Postgres tests are present but have not run. |
| FIX-09 | `manual_only` had no corresponding explicit manual analysis route. Pending jobs could also be claimed after their protocol was disabled. | Add `analyze --protocol ID`, protocol-scoped claims and enabled-protocol checks. New Postgres tests remain unrun. |
| FIX-10 | An object-store failure before sending a Telegram attachment was labeled UNKNOWN even though no send was attempted. | Track whether submission started; preparation failures are retryable/failed, while ambiguous submissions remain UNKNOWN. Postgres regression remains unrun. |
| FIX-11 | Telegram summary slicing counted Python characters; astral Unicode could exceed a UTF-16-based text budget. | Bound the summary conservatively in UTF-16 units; regression passes. |

“Code changed” is not synonymous with “verified.” The table deliberately distinguishes passing local tests from database behavior that still needs execution.

## Required work still open

The authoritative work register is `docs/RELEASE_REQUIREMENTS.yaml`. Every entry has a stable ID, owner, specification reference, acceptance condition and evidence field. It has no “known limitation,” “accepted by documentation,” or implicit “deferred and done” completion state.

| Gate | Why it blocks first production | Required closure |
| --- | --- | --- |
| PROD-01 — database and container verification | The core integrity, locking, retry and delivery claims still depend on unrun tests. | Run all integration tests against Postgres 18, build and validate Docker, and record the exact tested commit and CI run. |
| PROD-02 — GitHub history | A field added and reverted across two commits is currently invisible to this implementation. | Implement the watcher, pagination, all intervening commits/diffs, force-push handling, rate limits and baseline/retry tests. |
| PROD-03 — frontend assets | Script URL fingerprints alone miss a feature flag changed inside a bundle at the same URL. | Archive configured asset bodies, detect content and inventory changes, and preserve incomplete-fetch failures. |
| PROD-04 — complete input budgets | Diff slicing does not bound coverage metadata, prior reports or the final synthesis request. A job over its immutable chunk cap can exhaust retries without a usable recovery path. | Bound complete requests, preserve every changed hunk, and support resumable/replanned oversized work with audit history. |
| PROD-05 — durable storage and restore | Local file data is fsynced, but directory-entry durability is not yet established before DB commit. The selected remote object store and restore process have not been tested. | Implement/test durable publication, verify the real backend and restore a matching database/evidence backup. |
| PROD-06 — actual source coverage | Two sitemap/homepage configs do not establish the reference protocols' official technical footprint. Endpoint HTTP 200 responses are not clean collector baselines. | Verify sources and representations, complete both baselines, demonstrate unchanged/no-AI behavior, and enrich inventory additions with new-page context. |
| PROD-07 — scheduler reconciliation | Cadence/pinning is sticky after subscribers disappear; promotion does not reschedule an already distant due time; discovered pages use a fixed cadence. Batch scheduling can leave unrelated hosts waiting. | Reconcile active subscription policy and ownership, implement timely promotion/configurable page cadence, and test host fairness and stale workers. |
| PROD-08 — visible failure and analysis recovery | CLI failure inspection exists, but production health signaling must expose terminal work instead of requiring someone to notice a quiet channel. | Run recovery tests and verify health/workflow failures and successful operator recovery. |
| PROD-09 — Telegram end-to-end behavior | Real delivery is untested. Long preparation can outlive the stale-send interval, and reconciliation must not race an active attempt. Focus can repeat general findings in different words. | Bound/fence attempts, test acknowledgement-loss and concurrent recovery, verify the real channel, and prevent repeated general findings from generating duplicate focus alerts. |
| PROD-10 — runtime network and deployment | DNS validation and the HTTP transport resolve separately. The actual runtime's network, durable volumes and restart behavior have not been verified. | Use a tested pinned transport or outbound network policy and validate the complete deployment path. |

Implementation and test ownership remains with Codex. The owner supplies credentials/account access for live services; that dependency does not transfer unfinished engineering work to the owner.

## Further specification work is also unfinished

These remain tracked as OPEN under later gates, not waived: richer historical evidence retrieval and the complete report format (SPEC-01); daily digest and coverage-health delivery (SPEC-02); priorities, usage accounting and budget warnings (SPEC-03); retention and operator replay (SPEC-04); and 200–500 protocol load/soak qualification (SCALE-01).

The later gate is an implementation sequence, not permission to claim the full specification is delivered. Any future proposal to permanently omit a requirement must be presented as a scope change rather than relabeled as a limitation. Optional cost triage must not veto the required broad assessment.

Some constraints remain inherent and need explicit operating behavior: polling preserves observed transitions but cannot guarantee capture of an unobserved change/revert between polls; Telegram acknowledgement loss cannot provide a universal exactly-once guarantee. Those constraints require measured cadence, durable evidence, explicit UNKNOWN handling and tested reconciliation, rather than a false guarantee.

## Release discipline

Run:

```bash
uv run protocol-intel release-check
uv run protocol-intel release-check --target full_specification
uv run protocol-intel release-check --target scale
```

The command returns nonzero while included requirements are open or unverified. The separate **Release readiness** workflow runs that check. A requirement cannot be marked verified without a recorded evidence reference. The register is an auditable record, not a substitute for executing those tests; reviewers must inspect the cited results and tested revision.

Ordinary unit CI and development/validation commands remain available so unfinished work can be tested. A green unit suite is not a production release decision, and this check does not itself prevent someone from manually starting development commands. Do not activate production monitoring until the required gate has actual evidence of closure.

## Verification performed

- **47 local tests passed; 16 Postgres integration tests skipped.** Skips are not acceptance evidence.
- Ruff lint/format and mypy passed. Both starter configs validate. The CLI registers the new scoped analysis, retry and release-check commands.
- No model calls, channel messages, source baselines or production deployment were performed in this review.
- GitHub installation and selected-repository access were confirmed through the connector; no code was published.
- The first-production readiness check is expected to fail with all ten requirements still open/unverified.

## Order of work from here

1. Run real Postgres/container CI on a clearly labeled development branch when code publication resumes; fix failures before relying on integrity claims.
2. Complete storage durability, recovery, complete-request budgets and scheduler/network controls. These support every collector and alert.
3. Implement the GitHub history and frontend asset adapters, then verify the reference protocols' actual coverage.
4. Complete credentialed baselines, no-change checks, changed-evidence analysis, Telegram delivery/reconciliation and a restore exercise.
5. Record the evidence and close the first-production gate. Continue the remaining specification and scale gates without relabeling them as completed.
