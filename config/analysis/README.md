# Optional watch themes

No file is required here. Every protocol inherits broad general analysis.

To add questions for a particular protocol, create `<protocol-id>.yaml`:

```yaml
mode: always_deep
focus:
  - Is there evidence of a new shared-collateral product?
```

General assessment does not receive these questions. An additional focus job can
add findings after the general result is saved. It cannot veto a general alert.
Use `mode: manual_only` to retain a protocol's changes without automatically
queuing its analysis. Cheap triage is intentionally deferred pending evaluation.
