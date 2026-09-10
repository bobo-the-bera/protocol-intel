# Optional watch themes

No file is required here. Every protocol inherits broad general analysis.

To add questions for a particular protocol, create `<protocol-id>.yaml`:

```yaml
mode: tiered
focus:
  - Is there evidence of a new shared-collateral product?
```

General assessment does not receive these questions. An additional focus job can
add findings after the general result is saved. It cannot veto a general alert.
Use `mode: manual_only` to retain a protocol's changes without automatically
queuing its analysis. The default `tiered` mode performs broad inexpensive screening
and escalates uncertainty and possible importance. Use `always_deep` to opt a
protocol into the legacy direct deep path. Optional focus is a separate deep pass;
adding focus questions adds cost and does not narrow the general pass.
