# Forecasting method

Independent tracks exist because a single narrative absorbs base rates, current news, and resolver quirks into one number. Splitting them makes disagreements inspectable.

## Tracks

- **Base rate:** reference class, historical frequency, how this case differs.
- **Current evidence:** latest official prints, leading indicators, institutional outlooks.
- **Skeptic:** overturning evidence, correlated assumptions, wording and resolver-behavior risk.

Tracks do not receive other tracks’ probabilities or writeups. Sequential execution is an implementation detail, not information sharing.

Subquestions are generated from the contract and track charter. They are capped by the profile (default: four per track, three search hits retained, two fetches).

## Evidence

Search hits are ranked with a deterministic heuristic that prefers official domains, HTTPS, and dated documents, and that downranks social posts. Hostname checks use exact domain boundaries. Fetched pages are stored with URL, title, publisher, timestamps, excerpt, hash, and eligibility. Backtests reject publications or snapshots after `as_of` and never fall back to the current page. Current search snippets may discover URLs but are not treated as evidence after the cutoff. Every source shown in a report is a stored `EvidenceItem`. The model is not allowed to invent URLs. See `docs/EVIDENCE_CUTOFF_POLICY.md`.

## Aggregation

For each successful track probability `p`:

1. Clip to `[0.02, 0.98]`.
2. Convert to logit: `ln(p / (1 - p))`.
3. Take the equal-weight mean of track logits.
4. Shrink 10% in logit space toward the base-rate track, or toward 0.50 if that track failed.
5. Invert the logit.

Failed tracks are omitted. Two tracks can still produce a number; the missing track is visible on the report. The LLM may summarize disagreement after aggregation; it never picks the final probability.

## Resolver risk

The skeptic track estimates the chance that official scoring diverges from a casual reading. That risk is a track output, not a hidden fudge factor applied after aggregation.

## What this is not

The ensemble is not a calibrated probability. No reliability diagram is shown until 20 resolved predictions exist. Historical mode is an evidence-cutoff backtest, not a leak-proof simulation of a past forecaster.
