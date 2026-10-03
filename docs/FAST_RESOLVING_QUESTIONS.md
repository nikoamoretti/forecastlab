# Fast-resolving questions (FRED ICSA and DGS10)

Monthly BLS questions take weeks to resolve. Two FRED-sourced indicators let a
prospective cohort include questions that resolve within days. Autopilot,
question suggestions and BLS release discovery are unchanged: they still select
only the three BLS indicators (`forecastlab.macro.bls_indicators()`).

| Indicator | FRED series | Cadence and period | Units | Lineage | Expected release |
| --- | --- | --- | --- | --- | --- |
| `jobless_claims` | `ICSA`, initial claims, seasonally adjusted | weekly; week-ending Saturday `YYYY-MM-DD` | `claims` | `agency:dol` | DOL advance figure, the following Thursday 8:30 America/New_York |
| `treasury_10y` | `DGS10`, 10-year constant-maturity yield, not seasonally adjusted | daily; a weekday `YYYY-MM-DD` | `percent` | `agency:frb` | After the trading day; H.15/FRED usually post it the next business day |

`MacroSpec` validates the period by cadence. A weekly period must be a Saturday,
and a daily period must be a weekday. A daily release must be on or after the
next weekday. Monthly BLS periods remain `YYYY-MM`, and BLS contract templates
are byte-for-byte unchanged.

## Contracts and evidence

FRED contracts name the series and its first published value. Their
authoritative source is `https://fred.stlouisfed.org/series/<ID>`. Their
fallbacks are the ALFRED series page plus `https://www.dol.gov/ui/data.pdf`
(ICSA) or `https://www.federalreserve.gov/releases/h15/` (DGS10).

During live forecasting, `fetch_macro` reads the keyless
`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>&cosd=<start>`. It skips
blank or `.` values and keeps only observations strictly before the target. It
fails closed when the latest observation is older than 14 days (weekly) or 7 days
(daily). History is capped at 104 weekly or 130 daily observations, so the root
evidence packet stays well inside its 24,000-token reserve. Backtests of FRED
series raise `historical_fred_series_not_supported`.

`root_event_ensemble_v1` and `statistical_baseline_v1` read macro snapshots. The
statistical baseline passes optional `fetch_macro` arguments that widen only its
own FRED history to 156 weekly or 520 weekday observations; the capped history
above still bounds the root evidence packet (see
[the statistical baseline](STATISTICAL_BASELINE.md)). The cohort's other methods
(`single_model_forecaster_v1`, `three_track_strict_forecaster_v1`) see the
contract plus their own search.

## Resolution evidence at cohort freeze

`root_event_ensemble_v1` needs usable evidence for the `resolution`,
`reference_class` and `current_conditions` sections. The FRED snapshot covers
the last two. For the first, a cohort freeze now retrieves one official
publication document per FRED indicator, from the agency that publishes the
series:

| Indicator | Document | What its quotation shows | Lineage |
| --- | --- | --- | --- |
| `jobless_claims` | `https://www.dol.gov/ui/data.pdf`, the DOL Unemployment Insurance Weekly Claims news release (PDF) | The embargo time (8:30 a.m. Eastern, Thursday), the week-ending Saturday whose advance seasonally adjusted figure it publishes, and that the previous week was revised | `agency:dol` |
| `treasury_10y` | `https://www.federalreserve.gov/releases/h15/`, the Federal Reserve Board H.15 Selected Interest Rates release (HTML) | That the release is posted daily Monday through Friday at 4:15pm and not on holidays, its release date, and the dates it covers | `agency:frb` |

Both are public U.S. government documents. They are fetched keyless with the
public-data user agent, with no redirects, an exact content type and a 2 MB
ceiling. Both paths are allowed by the hosts' robots.txt. FRED's own series
pages are not used. The original bytes are retained in the artifact store
before parsing. The quotation is one contiguous passage of the
whitespace-normalized document text. It is checked again, verbatim, against
text re-extracted from the retained bytes, whose SHA-256 must match the stored
artifact.

Neither document states the target observation's release date, and the
evidence item does not claim that it does. It is attached only when every check
below passes:

- The document is recent: at most 8 days old for claims and 5 days for H.15,
  in New York calendar days, and not dated after its retrieval.
- The document follows the pattern the contract assumes. For claims, the
  release is the Thursday 8:30 New York time five days after the week it
  reports. For H.15, the release date is the weekday after the last date it
  covers.
- The target lies after the latest period in the document.
- The contract's expected release follows the same pattern. For claims, it is
  `claims_release_at(target)`. For DGS10, its New York date is the weekday after
  the target; the posting time is not compared.

The evidence item has the BLS schedule item's shape (`evidence_assessment_v2`,
`background`, `required_sections: ["resolution"]`, `primary_source: true`). It
adds `publisher` and a `release_pattern` audit record, in which
`target_release_stated_in_document` is always `false`. The frozen manifest
records its claim id, URL and document SHA-256 under `official_schedule`, the
same as for BLS entries. When a check fails, the manifest records the gap
instead, and the root method keeps withholding unless research supplies
resolution evidence:

| Gap | Meaning |
| --- | --- |
| `official_release_document_unavailable` | Network error, non-200 or redirect, wrong content type, or oversized response |
| `official_release_document_not_retained` | The artifact store did not accept the bytes |
| `official_release_document_unrecognized` (or an `official_release_pdf_*` code) | The expected passage is missing, ambiguous or inconsistent |
| `official_release_document_stale` / `official_release_document_date_invalid` | Too old, or dated after its retrieval |
| `official_release_pattern_unverified` | The document breaks the assumed pattern, for example a holiday-shifted release |
| `official_release_document_not_before_target` | The document already covers the target |
| `official_release_time_not_verified` | The contract's expected release does not follow the pattern |
| `official_release_document_hash_mismatch` / `official_release_quote_not_verbatim` | Retained bytes or quotation failed re-verification |

The parsers are in `forecastlab.fred_release_documents`, and retrieval and the
evidence item are in `forecastlab_api.official_sources`. The lookup runs only
when `cohort_schedule_evidence` is enabled, before the freeze lock, and a
failure never blocks a freeze. The documents current when the
`prospective_fast_fred_batch_20261003` cohort froze (the DOL release of
2026-10-01 and the H.15 release of 2026-10-02, kept as test fixtures) pass every
check for all 7 of its questions in an offline test. That batch itself ran
before this change and is not re-run.

## Outcome adjudication

`official_fred_initial_release_v1` (in `forecastlab_api/official_fred_outcomes.py`)
resolves on the initial release. That is the value in the earliest ALFRED vintage
containing the observation. The keyless vintage CSV is
`https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=<ID>&cosd=<obs-14d>&coed=<obs>&vintage_date=<YYYY-MM-DD>`,
whose value column is named `<ID>_<YYYYMMDD>`.

ALFRED clamps a future vintage date to its current date and changes that header,
so a mismatched header means "not published yet". The first vintage is the
first date `V` whose vintage contains the observation while `V - 1 day` does
not. Both CSVs are stored, and the amendment records the vintage date and URL.

The outcome uses Decimal comparison and the existing append-only
amendment/outcome machinery. Dispatch is by `SERIES[indicator]["source"]`, so
BLS entries still use `official_macro_first_release_v1`. A value not yet in any
vintage is a retryable `fred_initial_release_not_yet_published` and is retried
at most every 30 minutes. A blank first-vintage value, such as a holiday,
records a terminal `fred_initial_release_value_missing` and no outcome. The
owner should cancel that entry.

## Selection rule

`forecastlab.fast_questions.propose_fast_questions(now, snapshots)` is a fixed,
non-optimized rule that returns `CohortQuestionIn`-compatible dicts:

- **Claims (2 questions).** It takes the next two week-ending Saturdays whose
  Thursday 8:30 New York release is after `now`. In UTC that is 12:30 during
  daylight time and 13:30 during standard time. The event is
  `claims-<release date>`.
- **DGS10 (5 questions).** It takes the next five weekdays strictly after
  `now`'s UTC date. Each is released the following weekday at 21:00 UTC. The
  event is `dgs10-<observation date>`.
- **Threshold and comparison.** The threshold is the latest observed value
  before the target, and the comparison is `gt`.
- **Cutoff.** The cutoff is the earliest of three times: `now + 6h`, the release
  minus 30 minutes, and (for DGS10) the observation date's 13:30 UTC market open
  minus 30 minutes. A question without `now < cutoff < release` is dropped. The
  result never exceeds the cohort maximum of 10 questions.

The web UI was not updated. Create fast cohorts through
`POST /api/prospective/cohorts`, for example:

```python
from forecastlab.fast_questions import propose_fast_questions
from forecastlab.macro import fetch_fred_snapshot
from forecastlab.timeutil import utcnow

now = utcnow()
snapshots = {name: fetch_fred_snapshot(name) for name in ("jobless_claims", "treasury_10y")}
body = {"name": "Fast FRED pilot", "questions": propose_fast_questions(now, snapshots)}
```

## Limitations

- **Holidays.** The rule ignores holidays. A DGS10 market holiday has no
  observation, and its entry should be cancelled. A claims release moved by a
  holiday, such as Thanksgiving week, still resolves from the first vintage;
  only the contract's expected date is wrong.
- **Revisions.** ICSA's advance figure is revised the following week. The
  outcome deliberately uses the initial release, while the live forecasting
  history shows current (revised) values.
- **Calibration.** Daily yields are close to a random walk, and a threshold
  equal to the last value makes these questions near coin flips. They mainly
  test calibration and operations, not skill.
- **Granularity of `outcome_known_at`.** ALFRED vintages carry a date but no
  time. `outcome_known_at` is the scheduled release time when the first vintage
  falls on the scheduled date. Otherwise it is the start of the vintage date in
  New York.
- **Resolution evidence is a pattern, not a schedule.** The DOL and H.15
  documents show how and when the series is published. They do not announce
  the target's own release. A holiday-shifted release, a document that is not
  current, or an unrecognized page layout is a gap, never evidence. A holiday
  that falls on the target itself is not detected.
- **H.15 posting time.** H.15 is posted at 4:15pm. The contract expects DGS10
  at 21:00 UTC, which is 4:15pm or later only during daylight time. In
  standard time, 21:00 UTC (4:00pm Eastern) precedes the posting. Only the
  release date is checked, so this mismatch is not detected.
