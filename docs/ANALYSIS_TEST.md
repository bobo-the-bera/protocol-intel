# One analysis test and measured costs

Prerequisites: the eight repository secrets are configured, Telegram setup passed, and First run captured an initial baseline. First run 34411588023 completed the current NEAR and HyperLend baselines successfully.

1. Open **Actions → Analysis test → Run workflow**.
2. Choose branch **main**, protocol **near** (or **hyperlend**), then click the green **Run workflow** button once.
3. Open the new run. It should send a **TEST — BASELINE REVIEW** summary plus a Markdown document to the configured Telegram channel. The document is also shown in the run summary.
4. Review the reported input, cached-input and output token counts, estimated API dollars, archive bytes per protocol, whole database size, and monthly scenarios. Output token counts include reasoning tokens; they are not charged twice in the estimate.

The workflow makes at most one model submission for its run ID. It samples up to eight archived pages, spreads them through the URL inventory, and bounds excerpts and the complete serialized request to 48,000 bytes. Output is capped at 8,000 tokens. This is a paid test of existing snapshots, not a detected new change or a comprehensive baseline analysis. These test limits do not restrict production collection or the mandatory general analysis. Optional focus questions are not supplied.

## Reruns and failures

**Re-run jobs on the same run** preserves the run ID and reuses the saved API response. A new **Run workflow** creates a new paid test. A report already acknowledged by Telegram is not automatically sent again on a same-run retry.

The exact request and raw provider response are archived, and usage is saved before validating the generated findings. An incomplete response or invalid evidence cannot become a successful report. A model timeout or a crash between submission and receipt persistence is deliberately treated as an uncertain outcome: the same run will not submit again. Inspect the run logs and OpenAI usage before requesting a new test. Usage is printed immediately when the provider returns it, including when later report generation fails. A timeout without returned usage cannot be measured locally.

Telegram permission is checked before the paid call. Definite delivery failures retain the report; ambiguous submissions require checking the channel and resolving the outbox status before resending. See [TELEGRAM.md](TELEGRAM.md). Only this test's report is delivered; unrelated pending alerts are not drained.

## What the storage and cost numbers mean

Archive measurements list all objects in the application's `sha256/` and `diagnostics/` prefixes. Raw and normalized versions, frozen model inputs, raw test responses, and reports are attributed through their stored references. Identical content is counted once globally. Per-protocol totals can overlap for shared objects; exclusive bytes show content owned only by that protocol. The snapshot is measured before uploading the new report itself. Other bucket prefixes and provider backup storage are outside that measurement.

Postgres reports the physical size of the whole current database, including shared indexes and catalogs. This is not a Neon invoice or an exact per-protocol database allocation. Neon compute and account billing history are not accessible from the application credentials.

The report estimates 1, 10, 100 and 1,000 calls with this test's measured token workload. Real changes may require several chunks, synthesis and optional focus calls. Unchanged checks normally make no model calls. To estimate a new protocol, use a comparable baseline's exclusive archive size for an initial scenario, then measure new bytes and actual calls over a representative period. Change frequency and source size determine maintenance costs; protocol count alone does not.

Pricing references checked 2026-09-09:

- [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol): standard input $4/million tokens, cached input $0.40/million, output $20/million. The estimate allows for cache-write premiums and the documented large-context multiplier. The stored price table refuses an estimate for unrecognized models or after its recheck date; tokens remain available. Taxes, discounts and invoices are separate.
- [R2 Standard](https://developers.cloudflare.com/r2/pricing/): $0.015/GB-month, $4.50/million Class A operations, $0.36/million Class B operations, with account-wide free allowances and billing-unit rounding. Small incremental storage can fit existing allowances; current size does not measure monthly growth or request totals.
- [Neon](https://neon.com/pricing): Launch reference $0.106/CU-hour and $0.35/GB-month; plan limits and shared compute matter. No invented per-protocol database charge is assigned.
- [GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions): standard hosted runner minutes are free for this public repository; other runner types and artifact/cache billing differ.

This controlled test does not complete the production release requirements. [RELEASE_REQUIREMENTS.yaml](RELEASE_REQUIREMENTS.yaml) retains outstanding acceptance work and must pass before full production readiness is claimed.
