# Add a protocol

In the repository-connected assistant chat, a protocol name is enough to begin: **“Add Ethena.”** The assistant should verify the protocol identity and official public footprint, create the source configuration, run validation, and commit it. Once a runtime is available, baseline the new protocol and resume monitoring. Ambiguous names may require identity clarification. Source discovery is an assistant-assisted onboarding step in this release, not an autonomous search service inside the worker.

No statement of “what is important” is required. Keep the `profile` neutral and factual, because it is shared context for general analysis. Put personal objectives only in the optional focus file.

## Collector configuration

Create `config/protocols/example.yaml`:

```yaml
schema_version: 1
id: example
name: Example Protocol
profile: Public infrastructure for the protocol's documented products.
enabled: true
sources:
  - id: docs
    kind: sitemap
    url: https://docs.example.org/sitemap.xml
    interval_seconds: 21600
    max_pages: 1000
    max_sitemaps: 100
  - id: homepage
    kind: page
    url: https://example.org/
    interval_seconds: 3600
    pinned: true
```

Replace all example URLs with verified official endpoints. Supported kinds are `page`, `json`, and `sitemap`. Unsupported kinds fail validation. Sitemap-discovered pages initially poll hourly and may slow as they remain stable. `allowed_hosts` must explicitly include any needed cross-host redirects or child sitemap hosts. Keep it limited to the verified official footprint.

Avoid keyword filters and broad ignore expressions. `ignore_patterns` is for explicitly understood mechanical lines and matches complete lines only. `include_prefixes` can scope a shared sitemap to the correct project; it should not encode a thesis. Raise inventory limits deliberately if discovery reports a limit error.

## Optional additive focus

Only if desired, create `config/analysis/example.yaml`:

```yaml
mode: always_deep
focus:
  - Are any newly documented capabilities relevant to cross-chain execution?
```

The general assessment does not see this file's questions. The focus assessment runs separately after general completion and cannot remove any finding or alert from it. Removing the focus file restores general-only analysis; it does not narrow collection.

## Validate and activate

```bash
uv run protocol-intel validate
uv run protocol-intel sync-config
uv run protocol-intel baseline example
uv run protocol-intel status
```

Use Docker equivalents or Monitor task `baseline` with protocol `example`. Do not run database commands until the production connection and evidence backend are configured. Restart/recreate the Docker worker after committing config changes; Actions consumes `main` at the next run.

Config validation completes before database sync. Changes to acquisition or extraction policy create a distinct physical source baseline while preserving its previous history. Cadence and focus changes do not change source identity. Compatible sources can be shared across protocols, and a new subscriber starts at the current sequence instead of replaying old alerts.

Open production requirement PROD-07 covers cadence/subscription reconciliation and sitemap retirement; those behaviors are not considered complete. `manual_only` excludes automatic cluster creation; use `uv run protocol-intel analyze --protocol example` to explicitly analyze that protocol after its cluster window closes. Use `retry-analysis JOB_ID` to requeue a FAILED job with its original inputs and successful chunks. Database verification of these new paths is still pending under PROD-01/08.
