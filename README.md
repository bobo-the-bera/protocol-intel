# Protocol Intelligence Monitor

Monitor public protocol documentation and web surfaces, preserve exact changes, and send consequential findings to a Telegram channel.

**Pre-release implementation: first-production acceptance is blocked.** The final review found missing required collectors and unverified durability/recovery behavior. See [the review](docs/PREPUBLICATION_REVIEW.md) and [open release requirements](docs/RELEASE_REQUIREMENTS.yaml). The current code contains NEAR Intents and HyperLend starter configs, Postgres state, evidence storage, broad analysis, Telegram delivery and deployment tooling; their existence does not establish production readiness.

## Start here

1. [Connect your Telegram channel](docs/TELEGRAM.md). This can be tested from GitHub without a server, database, or OpenAI key.
2. [Configure and run the monitor](docs/DEPLOYMENT.md). Choose a persistent Docker host or the supplied GitHub Actions workflow with external Postgres and S3/R2.
3. Baseline `near` and `hyperlend`. A first observation never produces a change alert or model call.
4. Close and verify the first-production requirements before enabling the daemon or hourly workflow. `uv run protocol-intel release-check` currently exits unsuccessfully by design.

## Broad analysis is the default

Every real normalized change enters the general assessment, regardless of size or whether it matches an existing objective. The model is asked to look for consequential and unexpected developments, separate observation from inference, and state uncertainty. There is no mandatory per-protocol thesis and no keyword or AI triage gate in this release.

Optional watch questions run in a **separate, additive assessment** after the general report is committed. The general model input excludes those questions, and prior context contains only general assessments. A focus assessment cannot suppress a general finding or alert. Collection never reads watch questions.

This prevents a configured thesis from narrowing the pipeline; it does not guarantee that a model will recognize every important implication. Exact evidence remains available for later review.

## Included now

- Recursive sitemap discovery; same-host Markdown preference with HTML fallback; HTML, text, and JSON page monitoring.
- Conditional HTTP requests, explicit host scope, response size limits, shared per-host concurrency and cooldowns, adaptive polling, and recoverable failures.
- Postgres migrations, compatible source sharing, explicit baselines, occurrence-based events, expiring leases, and stale-worker fencing.
- SHA-256 evidence on a persistent local volume or S3-compatible object store. Repeated transitions such as A → B → A → B are all recorded.
- Fixed-window change clusters, complete diff chunking, schema-validated model responses, cached analysis chunks, archived model inputs and usage, and evidence-linked Markdown reports.
- Telegram summary plus full Markdown evidence attachment, a durable delivery outbox, and explicit handling of uncertain sends.
- Credential-free unit tests, real Postgres integration tests in CI, a non-root container, and operator commands.

The starter configs cover the listed docs and homepages. They are **not a claim of complete NEAR or HyperLend coverage**. Missing GitHub history and frontend asset adapters block the first-production release. Historical context, daily digest, cost controls, retention and scale qualification also remain open in the [release register](docs/RELEASE_REQUIREMENTS.yaml); they are not waived by appearing in a future stage.

## Add another protocol

You can ask the assistant in the repository-connected chat: “Add Ethena.” The assistant should verify official sources, add configuration, validate it, commit the change, and baseline it in the configured runtime. Optional interests can be supplied separately. The running Telegram bot is an output bot and does not interpret onboarding prompts.

For a manual config, see [ONBOARDING.md](docs/ONBOARDING.md). No application code or analysis file is required for a protocol whose sources use supported adapters. Restart a Docker worker after changing config; the Actions workflow reads the current `main` branch each run.

## Development

Python 3.12 or 3.13 and `uv` are required. Production CI and Docker use Python 3.13.

```bash
uv sync --frozen
uv run protocol-intel validate
uv run ruff check src migrations tests
uv run mypy
uv run pytest -q
```

Postgres tests skip locally unless `TEST_DATABASE_URL` points to a disposable database with a name ending in `_test`. CI runs these tests against Postgres 18, applies migrations twice, and builds the runtime image. Tests mock all OpenAI and Telegram calls and do not crawl production sources.

Project decisions and release boundaries are recorded in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

For the prepared source-package handoff, see [validation results](docs/VALIDATION.md) and [publication instructions](docs/PUBLISHING.md).
