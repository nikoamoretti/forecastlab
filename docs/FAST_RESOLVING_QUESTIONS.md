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
