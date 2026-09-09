# Publish the prepared initial repository

**Update, 9 September 2026:** installation now exists and includes this repository. The owner requested a prepublication review, which found open first-production blockers. Publication remains paused for that review; no write-access test or remote code mutation has been performed since installation. See [PREPUBLICATION_REVIEW.md](PREPUBLICATION_REVIEW.md). The older 403 below is historical, not the current reason for pausing.

The initial source package was prepared for `bobo-the-bera/protocol-intel`. During the initial publication attempt, GitHub rejected the first file creation with HTTP 403, `Resource not accessible by integration`. The GitHub plugin was enabled, but its installation query returned no accessible GitHub App installations. No remote file or commit was created by that attempt.

## Resume publication through the assistant

Reconnect/configure the GitHub connection used by this chat and authorize the app for **bobo-the-bera/protocol-intel**. In the GitHub application's installation settings, include this new repository and complete any pending installation or permission update needed for code and workflow writes. Repository ownership alone does not give an integration write access.

Once the connection is updated, tell the assistant to resume publishing this prepared project. It should confirm the current remote state, publish an implementation branch, run the supplied CI, fix any failing checks, and merge the initial code. It should not claim a live monitor is running until runtime credentials and baselines are verified.

## Publish from your own authenticated Git client

Alternatively, extract the source ZIP, open a terminal in its `protocol-intel` directory, and publish using your own authenticated GitHub client. These commands assume the remote repository is still empty:

```bash
git init -b main
git remote add origin https://github.com/bobo-the-bera/protocol-intel.git
git add .
git commit -m "Add initial Protocol Intelligence Monitor"
git push -u origin main
```

Use your normal authenticated Git client for the push. Do not put an access token in a command, repository file, or chat. If the remote has acquired commits since this package was created, stop and reconcile those files; do not force-push over them.

After publishing, inspect the **CI** run. Its Postgres integration and Docker checks must pass before treating the pilot as deployment-ready. Then follow [TELEGRAM.md](TELEGRAM.md) and [DEPLOYMENT.md](DEPLOYMENT.md). Public repository code, workflow logs, and Issues should never contain runtime credentials.
