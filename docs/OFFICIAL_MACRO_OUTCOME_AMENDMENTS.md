# Official macro outcome amendments

`official_macro_first_release_v1` is a narrow, deterministic post-freeze
adjudication policy for first-release U.S. macro contracts. It does not amend a
cohort manifest, contract, prompt, forecast, or forecast version.

Nico directed this routine-signoff removal on September 22, 2026, **after**
the August CPI outcome became known. It is an openly post-freeze procedural
amendment to the September 4 private cohort, not a claim that the original
human-confirmation protocol was preregistered as automatic. The original
protocol remains preserved in `docs/PERSONAL_V1_PILOT_PROTOCOL.md`.

For a due approved macro contract, ForecastLab validates the frozen contract
against its `MacroSpec`, then tries the exact dated BLS archive URL. If BLS is
unavailable, it may use only the exact dated U.S. Department of Labor PDF that
republishes the original BLS release. It retains source URL and identity,
retrieval time, publication time, content hash, content-addressed artifact
location, parser version, and Decimal-based threshold result.

The system records the post-cutoff fact in an append-only
`OfficialMacroOutcomeAmendment`, followed by an append-only outcome row
attributed to `system:official_macro_first_release_v1`. This is not a human
confirmation and never impersonates an owner. Existing prospective cohort and
Autopilot score reporting then use the ordinary append-only outcome rows.

Missing, redirecting, malformed, wrong-period, revised/corrected, tampered, or
conflicting evidence is stored as an `exception`; it produces no outcome or
score. Transient unavailability is retried no more often than every 30 minutes;
ready, conflicting, and non-transient exception records are never overwritten.
