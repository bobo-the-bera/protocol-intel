# First production run

**Current status: scheduled NEAR/HyperLend docs monitoring and daily digests are implemented and tested in PR #7; the owner approved activation with a 72-hour automatic stop.** Merging the proposed Monitor workflow enables this scoped pilot by default. That explicit approval now covers a 72-hour trial of recurring collection, paid analysis and Telegram delivery. The first cycle starts a persistent deadline, and expiry disables Monitor. This pilot does not close the [full production requirements](RELEASE_REQUIREMENTS.yaml): missing repository/asset collectors, backup restoration and other acceptance work remain explicit follow-up work. Read [SCHEDULE_AND_DIGEST.md](SCHEDULE_AND_DIGEST.md) for timing and controls.

The repository supplies two runtime options. Use **Docker on a persistent host** for continuous polling. Use **GitHub Actions with external Postgres and S3/R2** for a small scheduled pilot without an always-on application server. Do not run both against different state stores if you expect one alert history.

The source of truth is Postgres plus the evidence store. Git, workflow artifacts, and Actions caches are not runtime state. Merging an enabled Monitor workflow changes subsequent scheduled runs; those cycles may collect, analyze real changes, and post to Telegram using the configured credentials.

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

This route uses the supplied **Monitor** workflow. It runs from `main`; manual jobs and the 15-minute schedule share a concurrency group. It requires externally persistent Postgres and an S3-compatible bucket because each Actions runner is temporary.

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
| `OPENAI_DEEP_REASONING_EFFORT` | `medium` | Deep review; must be supported by the chosen model |
| `OPENAI_SCREEN_MODEL` | `gpt-5.6-luna` | Inexpensive broad screening and sampled test |
| `OPENAI_SCREEN_REASONING_EFFORT` | `low` | Screening effort |
| `ANALYSIS_AUDIT_PERCENT` | `2` | Deterministic percentage of routine clusters sent for deep audit |
| `TELEGRAM_ALERT_MIN_IMPORTANCE` | `HIGH` | Immediate alert threshold; MEDIUM findings remain archived |
| `ANALYSIS_ENABLED` | `true` | Allow model analysis during a cycle |
| `NOTIFICATIONS_ENABLED` | `true` | Allow actual report delivery |
| `MONITOR_ENABLED` | `true` when unset | Allow scheduled 15-minute cycles; set `false` to pause |
| `COLLECTION_ENABLED` | `true` | Check due sources during a cycle |
| `DAILY_DIGEST_ENABLED` | `true` | Queue one digest per UTC day |
| `DAILY_DIGEST_HOUR_UTC` | `8` | Hour after which the prior day's digest is due |

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

### Operate the pilot after activation approval

The current eight secrets and verified baselines are reused. Scheduled cycles are enabled when `MONITOR_ENABLED` is unset or `true`; explicit `false` overrides are honored. Collection, analysis, notifications and daily digests default on in Actions. Local `.env` role defaults remain off.

Open **Actions → Monitor → Run workflow**, branch **main**, task **cycle** to check immediately. No protocol selection is needed for a cycle: it processes all enabled protocols. Review stage results and the Current monitor health summary. Set `MONITOR_ENABLED=false` in repository Variables to pause future scheduled cycles; it does not cancel a run already in progress.

Runs are scheduled at minutes 7, 22, 37 and 52 each hour. A source is fetched only when its own adaptive cadence is due. The ten-minute clustering delay normally makes a newly observed change eligible on the following scheduler cycle. GitHub schedules may be delayed or skipped, so these are intended trigger times, not an alert-latency guarantee. A continuously running worker is the path to tighter timing.

The daily digest covers the previous UTC calendar day and becomes due at 08:00 UTC. It is queued on the next actual cycle, normally the 08:07 trigger, and costs no new model call. A durable cursor catches up one missed day per cycle, beginning with the day before pilot initialization. See [digest semantics and recovery](SCHEDULE_AND_DIGEST.md).

Manual tasks: `collect` checks due sources; `analyze` explicitly requests model work; `digest` queues the next due daily report without calling AI or sending; `notify` delivers queued reports when notifications are enabled; `status` displays health. A normal `cycle` performs enabled stages independently, so collection failure does not suppress digest creation or queued delivery. Its recorded outcome and workflow exit expose failures. Unknown Telegram submissions require channel inspection before explicit reconciliation.

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
