# First production run

**Current status: blocked pending the [prepublication review's release requirements](PREPUBLICATION_REVIEW.md).** These instructions describe configuration and validation. Do not activate continuous production monitoring until `uv run protocol-intel release-check` passes with actual verification evidence. Manual baselines and connection checks remain available for validation.

The repository supplies two runtime options. Use **Docker on a persistent host** for continuous polling. Use **GitHub Actions with external Postgres and S3/R2** for a small hourly pilot without an always-on application server. Do not run both against different state stores if you expect one alert history.

The source of truth is Postgres plus the evidence store. Git, workflow artifacts, and Actions caches are not runtime state. No real baseline, model call, or channel notification is performed just by merging the code.

## Option A: persistent Docker host

Use a Linux host with Docker Engine and the Compose plugin. The included stack runs Postgres 18 and the monitor; it does not need an inbound web port. Host/container restarts preserve the named volumes. Back up the database and evidence independently of the host.

### Configure

```bash
git clone https://github.com/bobo-the-bera/protocol-intel.git
cd protocol-intel
cp .env.example .env
chmod 600 .env
```

Edit `.env` using your editor. Set `POSTGRES_PASSWORD` to a unique random hexadecimal value (for example, generate one with `openssl rand -hex 32`). The Compose file constructs `DATABASE_URL` for its internal database. A hexadecimal password avoids connection-string escaping issues.

Initially leave:

```dotenv
BLOB_BACKEND=local
BLOB_DIR=data/blobs
COLLECTION_ENABLED=false
ANALYSIS_ENABLED=false
NOTIFICATIONS_ENABLED=false
```

The `evidence` Docker volume mounts at `/app/data`. Local storage is suitable for this single-host pilot. Use the S3 settings below before the first baseline if you want remote object storage from the start; simply switching backends later does not migrate existing blobs.

Set `OPENAI_API_KEY` when ready for analysis. Set the two Telegram values from [TELEGRAM.md](TELEGRAM.md). Do not commit `.env`.

### Build, migrate, and baseline

```bash
docker compose build
docker compose up -d db
docker compose run --rm migrate
docker compose run --rm worker validate
docker compose run --rm worker baseline near
docker compose run --rm worker baseline hyperlend
docker compose run --rm worker status
docker compose run --rm worker failures
```

`baseline` only fetches sources without a successful prior observation. It never resets established history and never invokes AI. A partial baseline exits unsuccessfully with the remaining URLs and preserves all successful work. Rerun after the source retry delay to resume. Newly discovered pages also receive a silent first-fetch baseline; later inventory changes are independently preserved and analyzed.

`near` currently covers the NEAR Intents docs sitemap and NEAR homepage. `hyperlend` covers the HyperLend docs sitemap and homepage. Review source failures instead of treating a blocked page as unchanged.

### Test Telegram and start the worker

```bash
docker compose run --rm --no-deps worker telegram-check
docker compose run --rm --no-deps worker telegram-test
```

Once both baselines and the connection test are satisfactory, edit `.env`:

First close the release requirements and run `docker compose run --rm --no-deps worker release-check`. A green connection test alone does not satisfy the production gate.

```dotenv
COLLECTION_ENABLED=true
ANALYSIS_ENABLED=true
NOTIFICATIONS_ENABLED=true
```

Then start:

```bash
docker compose up -d worker
docker compose logs --tail 100 -f worker
```

The worker runs collection, analysis, and delivery as independent async loops. New changes normally wait for the ten-minute cluster window before analysis. Stable unpinned sources gradually check less often; pinned homepages retain their configured interval. An error resets the successful stability period when collection recovers.

To run collection alone, leave analysis and notifications false. Later analysis will process retained eligible changes. Set the Telegram destination before producing reports if you want those reports queued for that channel; this release does not automatically backfill reports created without a destination.

### Operations and upgrades

```bash
docker compose run --rm worker status
docker compose run --rm worker failures
docker compose stop worker
git pull --ff-only
docker compose build
docker compose run --rm migrate
docker compose up -d worker
```

Before upgrades, take a Postgres backup and a matching evidence backup; verify a restore into a separate database. Do not use `docker compose down -v` on the production stack: it deletes named volumes. Migrations are serialized with an advisory lock and destructive downgrades are intentionally unavailable.

## Option B: GitHub Actions pilot

This route uses the supplied **Monitor** workflow. It runs from `main`; manual jobs and the hourly schedule share a concurrency group. It requires externally persistent Postgres and an S3-compatible bucket because each Actions runner is temporary.

### Create persistent storage

1. Create a dedicated Postgres database in a service/account you control. Use its normal Postgres connection string, require TLS for an external connection (commonly `?sslmode=require`), and ensure GitHub-hosted runners can reach it. The migration user needs permission to create tables and indexes in this database. Prefer a direct connection or session pool that supports the migration transaction.
2. Create a **private** bucket in Cloudflare R2, AWS S3, or a compatible object store. Create credentials restricted to reading and writing this bucket. Do not configure automatic deletion of evidence objects.
3. For R2, copy the account's S3 API endpoint, usually `https://ACCOUNT_ID.r2.cloudflarestorage.com`, and use region `auto`. For AWS S3, leave the endpoint blank and set the bucket's actual region.

The application uses conditional writes (`If-None-Match: *`) and verifies hashes on reads. Confirm your chosen compatible provider supports conditional object creation.

### Repository secrets

Open <https://github.com/bobo-the-bera/protocol-intel/settings/secrets/actions> and create:

| Secret | Value |
| --- | --- |
| `DATABASE_URL` | Dedicated external Postgres connection string; use `postgresql://...` or `postgresql+psycopg://...` |
| `S3_BUCKET` | Private bucket name |
| `S3_ENDPOINT_URL` | R2/compatible S3 endpoint; omit for AWS S3 |
| `AWS_ACCESS_KEY_ID` | Bucket API access key ID |
| `AWS_SECRET_ACCESS_KEY` | Bucket API secret access key |
| `OPENAI_API_KEY` | OpenAI API key with access to the configured model; needed for analysis |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather |
| `TELEGRAM_CHAT_ID` | Verified channel ID or public `@username` |

The initial baseline needs the database and bucket secrets only, while the standalone Telegram setup workflow needs only Telegram credentials.

Open **Settings → Secrets and variables → Actions → Variables** and set, as needed:

| Variable | Initial/default value | Purpose |
| --- | --- | --- |
| `S3_REGION` | `auto` | Set an actual AWS region when using AWS S3 |
| `OPENAI_DEEP_MODEL` | `gpt-5.6-sol` | Explicit model; change if your API project uses another supported Responses/structured-output model |
| `OPENAI_DEEP_REASONING_EFFORT` | `high` | Must be supported by the chosen model |
| `ANALYSIS_ENABLED` | `false` | Allow model analysis during a cycle |
| `NOTIFICATIONS_ENABLED` | `false` | Allow actual report delivery |
| `MONITOR_ENABLED` | `false` | Allow scheduled hourly cycles |

### Establish the baseline

For initial setup, use the [First run workflow](https://github.com/bobo-the-bera/protocol-intel/actions/workflows/first-run.yml). Select `main`, leave the baseline checkbox enabled, and run it. It migrates the database, verifies S3/R2 conditional creation and exact byte readback, commits a diagnostic pointer, verifies it in a separate process, then baselines both starter protocols. Uncheck the baseline option to test storage alone. It has no OpenAI or Telegram credentials and makes no model calls or channel posts.

Successful diagnostic runs leave small objects under `diagnostics/conditional-write/` and `sha256/`, with a matching `storage_probes` database record. These are setup evidence, not protocol changes. This checks connectivity and persistence across processes; it does not establish backup restoration, full source coverage or production acceptance. A failed source keeps the run red while preserving successful snapshots; rerunning resumes missing baselines.

The per-protocol Monitor workflow remains available:

1. Open <https://github.com/bobo-the-bera/protocol-intel/actions/workflows/monitor.yml>.
2. Click **Run workflow**, branch `main`, task **baseline**, protocol **near**.
3. Inspect the run for `baseline_complete: true`. If false, inspect its listed failures and rerun after the retry delay. Successful pages are not rebaselined.
4. Repeat with task **baseline**, protocol **hyperlend**.
5. Run task **status** to inspect source coverage, analysis backlog, and failures.

No OpenAI calls or Telegram posts are made by `baseline`. Limits are explicit; inventories that exceed their configured limits fail instead of silently losing URLs.

### Enable the pilot

1. Complete the Telegram **check** and **test** workflow from [TELEGRAM.md](TELEGRAM.md).
2. Set `ANALYSIS_ENABLED=true` and `NOTIFICATIONS_ENABLED=true` in repository Variables.
3. Run Monitor task **cycle** manually and inspect the result. An unchanged source or baseline should produce no alert.
4. Finally set `MONITOR_ENABLED=true` to permit hourly runs.

Only enable the schedule after the **Release readiness** workflow passes with recorded verification evidence. The prepared implementation currently fails that gate because required work is unfinished.

The scheduled job is configured for minute 17 of each hour. GitHub schedules can be delayed or skipped, and inactive public repositories may have schedules disabled by GitHub. With hourly collection plus a ten-minute cluster window, an alert may need the following run to become eligible; this is not a minute-level service. Use the daemon for more timely monitoring.

Manual tasks: `collect` checks due sources; `analyze` explicitly requests model work regardless of the cycle analysis flag; `notify` requires the notification flag; `status` displays health. Do not use manual `analyze` unless you intend to incur API usage. Analysis jobs retain failures after three attempts, and unknown Telegram outcomes require explicit reconciliation.

Each cycle continues analysis/delivery for successfully archived evidence even if an unrelated collection fails, but the workflow still ends red to expose degraded coverage. Use `status` and `failures` to diagnose it.

## Evidence and recovery

Use an old/new hash in a report to retrieve the exact archived body:

```bash
uv run protocol-intel evidence SHA256_HASH restored-evidence.txt
```

Run this command with the same blob backend settings as production. An event references normalized old/new hashes, while its corresponding version also retains the raw response hash. Reports and exact model-input chunks are archived in the same store. A hash mismatch fails explicitly.

The pilot retains evidence without automatic pruning. Define retention and verified backups before expanding its scope. Database recovery and object-store recovery must preserve the same history; code redeployment alone cannot reconstruct lost evidence.

## What this deployment does not yet establish

The initial code is not proof that every official source has been found, that any protocol change will be interpreted correctly, or that the full project specification is complete. Live OpenAI account access, Telegram permissions, and S3 interoperability are verified when the owner's credentials are configured. GitHub history scanning, frontend asset bodies, large-scale soak tests, and broader operational tooling are later stages.

Official references: [GitHub scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [R2 S3 API](https://developers.cloudflare.com/r2/api/s3/api/), [S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html), [Postgres backups](https://www.postgresql.org/docs/current/backup.html).
