# Evidence cutoff policy

Publication time and source-availability time are different facts. `publication_date` records a source-attributed publication date when one can be discovered. `source_available_at` records the timestamp ForecastLab uses to establish eligibility, and `temporal_basis` identifies whether that proof is a publication date, verified snapshot date, or live retrieval date. Retrieval time is never copied into publication time.

## Live forecasting

Live execution may use a page whose publication date is unavailable when ForecastLab retrieves and content-hashes that page during the live run. The claim keeps `publication_date = null`, `publication_date_verified = false`, `source_available_at = retrieval_date`, and `temporal_basis = retrieval_date`. This proves only that the observed bytes were available during the live run; it does not establish when the page was first published or whether earlier versions had the same content. Reports must surface that limitation.

## What qualifies as historical evidence

For a general web source in backtest mode, a document is eligible only when:

1. ForecastLab retrieves an archive snapshot timestamped at or before `as_of`, or
2. A dedicated source adapter can prove the returned record is an immutable historical version available at or before `as_of`.

Local HTML fixtures may be used only when fixture evidence is explicitly allowed (demo or synthetic fixture experiments) and their frozen publication or snapshot timestamp is at or before `as_of`.

Production real evaluation adds a stronger execution boundary. Each included question must be covered by one frozen `HistoricalEvidencePacket` under `private_v1_historical_evidence_release_v1`. A `ready` packet contains only content-addressed bytes/text backed by a verified final Wayback capture or a registered immutable-version adapter. A reviewed `no_eligible_evidence` packet preserves searches, candidates, archive checks, and rejection reasons, remains in the evaluation denominator, and yields no evidence or imputed probability. The execution worker reads the verified offline bundle only; it cannot fall back to Tavily, Wayback, or a current page.

## Why current undated pages are rejected

A current webpage with no publication date, or with an old article date in the HTML, does not prove that the current bytes existed at a historical cutoff. In backtest mode those pages are rejected with `unverifiable_as_of` or `no_eligible_historical_snapshot`. Retrieval time cannot qualify historical evidence, even when the page is otherwise valid for a live run.

## Wayback behavior

CDX queries are built with `urllib.parse.urlencode` and include `to=<as_of>`, `filter=statuscode:200`, `limit=1`, and `sort=reverse`. Fragments are stripped before the query. ForecastLab keeps the newest eligible snapshot at or before the cutoff.

After the streamed fetch completes, the **final** Wayback response is verified, not merely the requested snapshot URL. `SafeResponse.final_url` is parsed for capture timestamp, archived original URL, and replay modifier (`id_` when present). Acceptance requires:

1. The final URL is a recognized Wayback replay URL.
2. The final capture timestamp is at or before `as_of`.
3. The final archived original URL matches the requested source after canonicalization.
4. Redirects did not escape to a live non-archive URL.

For accepted archived evidence, the verified final capture timestamp becomes `source_available_at` and the temporal basis is `snapshot_date`. The publication date may remain unknown because the verified capture independently proves availability. A capture after `as_of` remains ineligible.

For an explicitly registered immutable source version, the independently proven availability timestamp becomes `source_available_at` and the temporal basis is `immutable_version`. The adapter and version identities are frozen in the evidence release. A current URL or unregistered adapter cannot use this path.

Reject reasons: `final_snapshot_not_wayback`, `final_snapshot_after_as_of`, `final_snapshot_original_url_mismatch`, `final_snapshot_metadata_unparseable`. Evidence records store requested and final snapshot URL/timestamp, archived original URL, and `snapshot_verification_status`. A snapshot one second after `as_of` is rejected (`snapshot_after_as_of` or `final_snapshot_after_as_of`).

## Search snippets

Current search results may discover candidate URLs. Their snippets never enter the forecasting packet. A search-provider publication-date hint is retained for audit and may assist live metadata discovery, but it is not equivalent to a verified archive timestamp and cannot qualify historical evidence. Only fetched and independently verified historical documents do.

## Model-pretraining leakage

This is an evidence-cutoff backtest, not a proof that the model lacked later facts. Weights can still contain post-cutoff information.

## Remaining limitations

- Wayback coverage is incomplete.
- Dedicated immutable-version adapters exist only for fixture and explicitly proven sources.
- The repository contains the frozen-release boundary and synthetic test factories, not a certified real historical-evidence corpus. Real non-redistributable bytes must remain outside Git in a verified content-addressed bundle.
- Publication-date discovery is conservative and incomplete. Live undated pages remain usable with the visible label `Publication date unavailable; page observed during live run`.
- A live retrieval-basis claim proves observation during that run, not original publication time or historical content stability.
- Retrieval time never qualifies an historical document.
