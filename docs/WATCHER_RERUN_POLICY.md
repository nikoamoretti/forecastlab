# Watcher rerun policy

ForecastLab V1 treats watchers as change detectors only.

- A watcher check may record a changed endpoint and mark its question stale.
- A watcher never creates or launches a `ForecastRun`.
- `auto_rerun` is disabled and default-off at the API and persistence boundaries.
- Existing legacy opt-in values are normalized to disabled by migration `20260829_0029`.
- The user must explicitly choose rerun from the question report.
- That rerun passes through the ordinary profile, contract, graph, preview, provider-readiness, budget, and idempotency controls.

Manual evidence follows the same rule. Accepting a URL can mark a completed question stale, but it cannot overwrite a Forecast Version or start a replacement run.

This is a deliberate V1 safety choice. An automatic rerun could spend provider budget without contemporaneous review and silently change the current versioned answer. A future scheduler would require a separate cost, notification, idempotency, and authorization design; it is not part of V1.

FL-QF001 is cancelled. ForecastLab retains its original question-first workflow and does not include a Question Feed, discovery league, market probabilities, or an automatic rerun service.
