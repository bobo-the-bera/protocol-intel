# Cost-sensitive broad monitoring

The owner authorized tiered inference after the first sampled baseline test used 7,852 input tokens and 4,469 output tokens on GPT-5.6 Sol with high reasoning. Its recorded cache-write count gives an estimated token charge of $0.128637. That was a baseline sample, not a measured typical change alert. Its evidence validation failed; the paid receipt remains available through **Analysis receipt** without another API call.

## Routing implemented

| Situation | Action |
| --- | --- |
| First baseline or unchanged normalized content | Archive/check deterministically; zero model calls |
| New normalized changes | Luna, low reasoning, concise broad assessment of exact diffs |
| Confident routine changes | Store the cheap assessment; no deep call except selected audits |
| Possible importance, novelty, uncertainty, or medium-or-higher finding | Sol, medium reasoning, reviews the full change cluster |
| Returned screening is incomplete, malformed, or omits evidence | Preserve its usage and escalate to Sol |
| Several individually routine chunks | Luna checks their combined assessments for connections; uncertainty escalates |
| Routine audit sample | Deterministic 2% of routine clusters receive deep review |
| Optional focus questions | A separate additive deep pass after the independent general report |
| API outcome is unknown | Stop visible work; never assume zero cost or automatically resubmit |

The model choice is based on documented prices and workload fit, not a measured recall benchmark on this project. Routine classifications can still miss signals. Audit findings and representative real changes must be evaluated before claiming acceptable production recall. No focus questions, keywords or diff-size thresholds narrow collection or the broad pass.

## Reducing token use without deleting evidence

- Change analysis sends diffs, not the unchanged document corpus. Source polling already uses conditional requests and normalized hashes.
- Healthy-source metadata is represented by coverage counts; exact coverage records stay in the job/report. Recent deep context is explicitly bounded and labeled as prior inference. The broad cheap pass has no historical thesis context.
- Short per-request evidence aliases replace long IDs in model output. The frozen contract retains the mapping back to original events; the JSON schema constrains valid references.
- Default output caps are 2,000 tokens for screening and 6,000 for deep review, including reasoning. The sampled test uses the cheaper model and the screening cap.
- Complete serialized requests are bounded to 48,000 bytes by further splitting evidence without losing diff characters. If a synthesis or metadata budget cannot fit, the job fails visibly with its successful work retained. No oversized input is silently treated as unimportant.
- Multi-chunk deep synthesis preserves original findings. Once a screen requests deep review, remaining redundant screens are skipped and the stronger model examines every chunk in that cluster.
- Stable system instructions and schemas avoid gratuitously changing request prefixes. Provider-reported cache hits and writes determine the estimate; cache savings are not assumed.

`model_calls` records a submission claim, exact archived input, raw response and usage before output validation. A report-upload failure can reuse both screening and deep responses. An incomplete deep response remains a visible unresolved job; retries do not buy the same truncated answer again. A process loss before receipt persistence remains an unknown paid outcome and requires inspection. The retained `always_deep` legacy mode does not use this new receipt mechanism; prefer `tiered` for production cost tracking.

## Pricing and scenarios

Official model prices checked 2026-09-09, standard tier, USD per million tokens:

| Model | Input | Cached input | Output |
| --- | ---: | ---: | ---: |
| [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | $0.20 | $0.02 | $1.20 |
| [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) | $4.00 | $0.40 | $20.00 |

Cache writes cost 1.25 times uncached input rates. Output usage already includes reasoning. The estimator accounts for reported cache writes, or reports a range if that count is absent. It supports these models and Terra, their documented large-context multiplier, and rejects unverified model/date estimates rather than assuming a price of zero.

At exactly the first test's token counts, Luna's price calculation would be about **$0.007326**, versus Sol's **$0.128637**—about 94% less for that call. This is a price-only comparison: models generate different token counts and may differ in quality. It is not a measured rerun.

Illustrative single-chunk workload, with all input charged as cache writes:

- Screening: 3,000 input + 300 output tokens → $0.00111/call.
- Deep review: 6,000 input + 2,000 output tokens → $0.07/call.
- 1,000 changed clusters/month, 10% escalating plus 2% audits of the remainder → $1.11 screening + $8.26 deep = **$9.37/month** in model costs.
- Direct deep review of all 1,000 comparable clusters → $70/month.

These are assumptions, not a forecast. More ambiguous changes, long diffs, focus jobs and multiple chunks increase cost. Unchanged polling is not a changed cluster. Storage, database compute, provider allowances and taxes are separate. Every tiered report includes the actual stage token counts and estimated model charges so forecasts can be replaced with measurements.

## Operations and remaining acceptance

New jobs default to `tiered`; existing queued payloads and saved paid requests retain their original settings. `config/analysis/<protocol>.yaml` can explicitly select `always_deep` or `manual_only`. Model/effort, audit rate and immediate Telegram importance threshold are configurable; see DEPLOYMENT.md and .env.example. Existing repository variables override new defaults.

The channel behavior is one daily UTC digest plus HIGH/CRITICAL alerts after change analysis. The [scheduled pilot](SCHEDULE_AND_DIGEST.md) aggregates saved events/results with no new model call for the digest. The proposed Actions roles default on when PR #7 is merged; automatic approval review requires explicit activation approval first. Explicit false repository variables pause them. Protocol priority tiers, daily/monthly budget warnings, complete oversized-job replanning, richer history retrieval, source coverage and production quality validation remain active requirements in RELEASE_REQUIREMENTS.yaml.
