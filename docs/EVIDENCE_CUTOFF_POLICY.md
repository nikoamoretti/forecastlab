# Evidence cutoff policy

## What qualifies as historical evidence

For a general web source in backtest mode, a document is eligible only when:

1. ForecastLab retrieves an archive snapshot timestamped at or before `as_of`, or
2. A dedicated source adapter can prove the returned record is an immutable historical version available at or before `as_of`.

Local HTML fixtures may be used only when fixture evidence is explicitly allowed (demo or synthetic fixture experiments) and their publication date is at or before `as_of`.

## Why current undated pages are rejected

A live webpage with no publication date, or with an old article date in the HTML, does not prove that the current bytes existed at the cutoff. In backtest mode those pages are rejected with `unverifiable_as_of` or `no_eligible_historical_snapshot`.

## Wayback behavior

CDX queries include `to=<as_of>`, `filter=statuscode:200`, `limit=1`, and `sort=reverse`. ForecastLab keeps the newest eligible snapshot at or before the cutoff. Snapshot URL, timestamp, discovery result, fetch status, and content hash are recorded. A snapshot one second after `as_of` is rejected (`snapshot_after_as_of`).

## Search snippets

Current search results may discover candidate URLs. Their snippets never enter the forecasting packet. Only fetched and verified historical documents do.

## Model-pretraining leakage

This is an evidence-cutoff backtest, not a proof that the model lacked later facts. Weights can still contain post-cutoff information.

## Remaining limitations

- Wayback coverage is incomplete.
- Dedicated immutable-version adapters exist only for fixture and explicitly proven sources.
- Live undated pages are allowed with an honest `published_at_unknown` label.
