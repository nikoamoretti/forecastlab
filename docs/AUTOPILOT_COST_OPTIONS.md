# Autopilot cost options

Assessment: 2026-09-05. These are implementation options, not deployed savings or changes to the frozen pilot method.

## Keep the useful baseline

Keep the $25 weekly cap, $5 per-run cap, three initial forecasts and two refresh ceilings, and three root estimates. The production ledger uses conservative model rates from `configs/pricing/models.yaml`; its costs are estimates, not provider invoices. Lowering an estimate alone does not save money. Reconcile provider invoices before claiming realized savings.

The application already uses deterministic question templates, a deterministic research graph, structured macro transformations, deterministic report assembly and inbox summaries, cached macro observations, and persisted provider checkpoints. These avoid writing-model calls and repeated completed work. Do not replace these with agents.

## First priority: avoid idle database wakeups

A 15-minute cron makes 96 checks per day. If every check wakes a 0.25-CU database for five minutes, 30 days imply 240 active hours, 60 CU-hours, and about $6.36 in compute at $0.106/CU-hour. Query execution time, autoscaling, other traffic, storage, restore history and tax are additional. This is a scenario calculation; the actual compute settings have not yet been verified.

A later scheduler version can read a small private object containing the earliest required database wake time. Quiet ticks can return before opening Postgres. Recompute that deadline after durable queue, policy, release, source-monitor and backup state changes. A missing, expired, malformed or inconsistent hint must cause a normal database reconciliation. Keep a bounded unconditional recovery tick so a failed hint update cannot strand an owner launch. Preserve the 15-minute prerelease stop and daily backup deadline. Test interrupted writes and duplicate cron delivery before rollout.

For comparison, one five-minute wake per hour would cost about $1.59/month in this same compute-only scenario; two per day about $0.13. Neither figure includes the other charges above or represents a deployment promise. Avoid adding a paid cache subscription solely to save a few dollars of database compute.

## Second priority: research one release, reuse its documents

Payrolls and unemployment share the Employment Situation release. Cache retained source bytes and question-independent extraction by source hash, extraction version and publication cutoff. Reuse across those questions while independently assessing relevance to each exact contract. Never reuse another question's probability, treat copies as independent sources, or allow later-publication information into an earlier cutoff. Continue to charge each physical provider attempt to its originating run and weekly ledger; cache hits incur no fictional provider charge.

This needs a new execution/prompt version and matched qualification runs before replacing the current profile.

## Third priority: spend research effort on actual gaps

The approved release document and validated BLS observation history already cover some resolution, current-condition and reference-class requirements. A future macro-specific executor can use deterministic coverage checks before commissioning model-planned searches. Search for missing drivers and contrary evidence, then stop once the defined evidence requirements and minimum adversarial checks are met. Keep all three blind root estimates. Compare abstention rate, latency, measured provider cost and resolved-question scores against the current method; lower cost alone is insufficient.

## Avoid false economies

Do not shorten restore retention, replace missing first-release evidence with revised values, skip outcome confirmation, weaken owner authentication, run unbounded retries, or label a timed-out investigation successful. Do not switch models mid-qualification. Batch APIs are a possible later fit for nonurgent research, but conflict with the current five-minute end-to-end allowance and need a separate execution mode.

## Sources

- Neon pricing: https://neon.com/pricing
- Neon compute management: https://neon.com/docs/manage/endpoints/
- GitHub OAuth registration: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app

## First live qualification measurement

CPI run `0548b8d4-7103-4c01-8b22-f0120a79a215` completed with three estimates in 219.8 seconds. The conservative ledger total was $0.51293: planning $0.04353, eight searches $0.064, eleven extraction calls $0.24737, evidence assessment $0.054135, and three final estimates $0.103895. The model was `gpt-5-mini-2025-08-07`. Extraction accounted for about 48% of the estimated cost; the three final estimates accounted for about 20%. This supports prioritizing evidence reuse and more selective extraction over removing forecasters. One run is not a representative cost benchmark or an accuracy result.
