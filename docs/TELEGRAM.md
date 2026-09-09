# Connect a Telegram channel

The monitor sends a concise material-change alert followed by a Markdown file containing findings, uncertainty, source URLs, evidence hashes, and exact diff excerpts. Baselines and unchanged checks produce no alerts.

You can complete the connection test entirely through Telegram and GitHub. You do not need to run code locally or buy an OpenAI key for this test.

## 1. Create a dedicated bot

1. Open Telegram and find the official **@BotFather** account: <https://t.me/BotFather>.
2. Send `/newbot`.
3. Choose a display name, for example `Protocol Intelligence`.
4. Choose an available username ending in `bot`, for example `your_protocol_intel_bot`.
5. Copy the bot token BotFather gives you. It looks like `123456789:...`.

Keep this token in secrets. Do not paste it into chat, a GitHub issue, a commit, or a browser URL. If exposed, revoke/regenerate it through BotFather and update the configured secret. A dedicated bot makes channel discovery predictable and avoids interfering with another application's webhook.

## 2. Create or choose the output channel

1. In Telegram, create a **channel**, or open the channel you want to use. Public and private channels are supported.
2. Open the channel's management/settings screen, then **Administrators → Add Administrator** (wording varies by Telegram client).
3. Search for your bot's exact username and add it.
4. Enable **Post Messages**. The monitor does not need rights to add admins, ban users, edit channel details, or delete messages.

A channel is a broadcast destination. This initial implementation intentionally checks that the destination is a channel.

## 3. Save the token in GitHub

Open:

<https://github.com/bobo-the-bera/protocol-intel/settings/secrets/actions>

Or navigate to **repository → Settings → Secrets and variables → Actions → Secrets → New repository secret**.

Create:

| Secret name | Secret value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | The exact token from BotFather |

Do not put the token in the Variables tab. Repository code and Actions logs may be public, so the setup command deliberately never prints the token or channel post bodies.

## 4. Find and save the channel ID

Use one of these methods.

### Public channel with a username

If its public link is `https://t.me/my_protocol_alerts`, create the repository secret `TELEGRAM_CHAT_ID` with the value `@my_protocol_alerts`. Include `@`; do not use the invite URL or channel display name. Continue to step 5.

### Private channel, or stable numeric ID preferred

1. After adding the bot as an administrator, publish this exact text **in the channel**:

   ```text
   protocol-intel setup
   ```

2. Open <https://github.com/bobo-the-bera/protocol-intel/actions/workflows/telegram-setup.yml>.
3. Click **Run workflow**, select branch **main**, choose mode **discover**, and run it.
4. Open the resulting run, open job **setup**, and expand **Run Telegram setup**.
5. Find `channel_id`. It will normally look like `-1001234567890`.
6. Return to repository Actions secrets and add `TELEGRAM_CHAT_ID` with that full value, including the minus sign. Do not include quotation marks.
7. You may delete the setup marker from the channel after discovery.

If more than one candidate is returned, identify the intended channel before setting the secret; the command never chooses one automatically. For a new dedicated bot, posting the marker in only the intended channel normally yields one result.

Discovery reads recent bot updates and does not send messages or delete webhooks. Telegram keeps pending updates for at most 24 hours, so post a fresh marker if necessary. If another application is consuming this bot's updates, use a dedicated bot instead.

## 5. Verify permissions, then send a test

1. Run **Telegram setup** again on `main`, this time in **check** mode.
2. Confirm the output shows your expected bot username, channel ID, `channel_type: channel`, and `can_post_messages: true`.
3. Run it once more in **test** mode.
4. Look in your channel for **“Protocol Intelligence Monitor — Telegram connection verified.”** The message identifies itself as a setup test.

`discover` and `check` are read-only. Choosing `test` sends exactly one test request; the setup command does not automatically retry an ambiguous send. If the workflow times out after submission, inspect the channel before running it again.

## 6. Enable actual monitoring output

A successful test proves the Telegram connection. Continuous monitoring also needs a configured runtime, persistent evidence storage, and an OpenAI API key. Follow [DEPLOYMENT.md](DEPLOYMENT.md).

The current pre-release has open production requirements. Complete channel testing now, but activate continuous monitoring only after the release-readiness gate is verified. Telegram setup does not close collector, storage or recovery gaps.

- **GitHub Actions runtime:** the two Telegram secrets above are already in the correct location. After configuring storage, baselining, and adding `OPENAI_API_KEY`, create repository Variables `ANALYSIS_ENABLED=true`, `NOTIFICATIONS_ENABLED=true`, and finally `MONITOR_ENABLED=true` for the hourly schedule.
- **Docker runtime:** GitHub secrets are not automatically copied to your server. Put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the server's `.env`, run the local check/test commands below, and set `NOTIFICATIONS_ENABLED=true` before starting/recreating the worker.

Collection and baselines do not use the model. General analysis is broad by default. Optional focus questions may generate a separate additive report; they do not control whether the general report is sent.

## Docker equivalents

After building the image and creating `.env` as described in the deployment guide:

```bash
docker compose run --rm --no-deps worker telegram-discover
docker compose run --rm --no-deps worker telegram-check
docker compose run --rm --no-deps worker telegram-test
```

For a Python checkout, the equivalent command prefix is `uv run protocol-intel`.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| “No setup marker found” | Post the exact marker after adding the bot; confirm it is in the channel, and run discovery within 24 hours. Check that no other application consumes this bot's updates. |
| “Already uses a webhook” | Use a new dedicated bot. The setup helper will not remove another application's webhook. |
| API error 401 | Token is wrong or revoked. Update `TELEGRAM_BOT_TOKEN`. |
| API error 400/403 or “not a channel” | Check `TELEGRAM_CHAT_ID`, bot membership, channel type, and Post Messages permission. |
| Workflow is missing | Open the Actions tab and enable Actions if GitHub asks. The workflow must be present on `main`. |
| Test succeeds but no real alerts | Complete runtime setup. Baselines and unchanged content are silent; changed evidence must close its cluster window and produce a material finding. Inspect Monitor `status` and `failures`. |
| `UNKNOWN` delivery | Inspect the channel for the report ID before retrying. The provider may have accepted the message even though its acknowledgement was lost. |
| Summary appears without attachment | Inspect the delivery outbox. Attachments are delivered separately and can fail independently; the archived report remains intact. |

An operator can reconcile a delivery after checking the channel:

```bash
# If the message is present, record its confirmed numeric message ID:
uv run protocol-intel resolve-delivery DELIVERY_ID sent --message-id 123

# Only after confirming it is absent, explicitly allow a retry:
uv run protocol-intel resolve-delivery DELIVERY_ID retry
```

Use the `failures` command to find delivery IDs. Resolving a summary as sent allows its pending attachment to proceed. Telegram does not provide a client idempotency key for `sendMessage`; this outbox deliberately does not promise exactly-once delivery across ambiguous network failures.

Official references: [BotFather tutorial](https://core.telegram.org/bots/tutorial), [Telegram Bot API](https://core.telegram.org/bots/api), [GitHub Actions secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions).
