# Evidence cutoff policy

## What qualifies as historical evidence

For a general web source in backtest mode, a document is eligible only when:

1. ForecastLab retrieves an archive snapshot timestamped at or before `as_of`, or
2. A dedicated source adapter can prove the returned record is an immutable historical version available at or before `as_of`.

Local HTML fixtures may be used only when fixture evidence is explicitly allowed (demo or synthetic fixture experiments) and their publication date is at or before `as_of`.

## Why current undated pages are rejected

A live webpage with no publication date, or with an old article date in the HTML, does not prove that the current bytes existed at the cutoff. In backtest mode those pages are rejected with `unverifiable_as_of` or `no_eligible_historical_snapshot`.

## Wayback behavior

CDX queries are built with `urllib.parse.urlencode` and include `to=<as_of>`, `filter=statuscode:200`, `limit=1`, and `sort=reverse`. Fragments are stripped before the query. ForecastLab keeps the newest eligible snapshot at or before the cutoff.

After the streamed fetch completes, the **final** Wayback response is verified, not merely the requested snapshot URL. `SafeResponse.final_url` is parsed for capture timestamp, archived original URL, and replay modifier (`id_` when present). Acceptance requires:

1. The final URL is a recognized Wayback replay URL.
2. The final capture timestamp is at or before `as_of`.
3. The final archived original URL matches the requested source after canonicalization.
4. Redirects did not escape to a live non-archive URL.

Reject reasons: `final_snapshot_not_wayback`, `final_snapshot_after_as_of`, `final_snapshot_original_url_mismatch`, `final_snapshot_metadata_unparseable`. Evidence records store requested and final snapshot URL/timestamp, archived original URL, and `snapshot_verification_status`. A snapshot one second after `as_of` is rejected (`snapshot_after_as_of` or `final_snapshot_after_as_of`).

## Search snippets

Current search results may discover candidate URLs. Their snippets never enter the forecasting packet. Only fetched and verified historical documents do.

## Model-pretraining leakage

This is an evidence-cutoff backtest, not a proof that the model lacked later facts. Weights can still contain post-cutoff information.

## Remaining limitations

- Wayback coverage is incomplete.
- Dedicated immutable-version adapters exist only for fixture and explicitly proven sources.
- Live undated pages are allowed with an honest `published_at_unknown` label.
