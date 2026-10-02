# Personal V1 implementation and pilot results

September 4, 2026. The personal workflow is implemented and remains opt-in. **The original 10-question, 30-assignment pilot failed its operational test.** A later, bounded repair check demonstrated a successful execution of the new method on one question. It does not replace the original pilot or establish forecasting accuracy.

The implementation retains FastAPI, the existing job worker, SQLite, and Next.js. It adds durable preparation and one-review launch, frozen contracts and settings, atomic spending limits, structured evidence assessments, a separate same-event ensemble, macro observations/vintages, nullable outcomes, paginated summaries, version selection, and prospective evaluation with append-only adjudication. Historical evaluation requirements remain unchanged.

## Original frozen pilot

Cohort: `ceef11c4-7854-49af-988e-a01e32912564`.
Manifest: `df34b8c4a8292bd11250b5d959e4d446ca7b88a8d9fc97c03706ef9e75f91ae3`.
Forecasting source commit: `28746d7`. Root profile revision: 1.
Model: `gpt-5-mini-2025-08-07`; search: Tavily.

| Method | Assigned | Valid probabilities | Withheld | Execution failures | Estimated cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| `root_event_ensemble_v1` | 10 | 0 | 9 | 1 | $4.453590 |
| `single_model_forecaster_v1` | 10 | 9 | 0 | 1 | $0.349665 |
| `three_track_forecaster` | 10 | 0 | 0 | 10 | $1.750270 |
| Total | 30 | 9 | 9 | 12 | **$6.553525** |

The root method's nine withheld results exhausted execution time during research. The other root execution reached the provider's output limit. The single-model failure referenced unsupported evidence. All ten three-track failures reported `invalid_structured_output:track_forecast`. These results are retained.

Nine successful baseline versions were initially mislabeled as abstentions because the personal summary queried an unflushed database insert. Their derived summaries were corrected after a regression test reproduced the problem in both baseline methods. Canonical probabilities, costs, contracts, assignments, and provider receipts were not changed. The original summaries and a before/after correction receipt are retained.

## Bounded repair check

Cohort: `b1884b10-f154-408c-8c5f-a8ab96c3f4c4`.
Manifest: `b4e89e1a9a64e258d93d5e01452d5b5e9a319e9e2f6668a06e5d86ee5d238ff2`.
Root profile revision: 2. This check reused one original question, with a **$3 total cap across all three methods**. It is a regression check, not an independent sample or a replacement pilot.

The question asks whether the first published seasonally adjusted U.S. unemployment rate for September 2026 will exceed 4.1%, resolving on the October 2 release. The approved contract and information cutoff were frozen before execution.

| Method | Result | Execution time | Estimated cost |
| --- | --- | ---: | ---: |
| Root ensemble | 37.376% | 184.764 seconds | $0.568905 |
| Single model | 30.0% | 12.663 seconds | $0.029960 |
| Three track | Structured-output failure | 82.808 seconds | $0.173525 |

The root estimates were 35%, 40%, and 38%, with a spread of 5 percentage points. All three referenced the identical contract, packet, event, deadline, and cutoff. Independent recomputation reproduced the stored aggregate. The packet contained 12 usable assessments, including the structured macro series, and covered the three required evidence sections. All four assessment/forecast model responses finished normally. The live result page rendered 37.4% without browser errors.

Revision 2 uses minimal reasoning for planning, extraction, and assessment, low reasoning for the estimates, and a 4,096-token output allowance. Model/search requests and physical retries now share one execution deadline. Late responses retain their cost but cannot become forecasts. Timeouts are explicit execution failures. The aggregation formula remains unchanged.

### Observed outcome (added 2026-10-02)

The September 2026 seasonally adjusted U-3 rate was first published on 2026-10-02 as **4.2%**, which is strictly greater than 4.1%, so the repair-check question resolves **yes**. The BLS public API (series `LNS14000000`, period M09 marked latest) returned 4.2, and the ALFRED `UNRATE` vintage dated 2026-10-02 shows the same first print. The BLS news-release page itself returned HTTP 403 to the verifying environment. This note does not create a database outcome or adjudication record; the local database still requires the normal append-only outcome entry.

| Method | Probability of yes | Brier score | Log loss |
| --- | ---: | ---: | ---: |
| Root ensemble | 37.376% | 0.3922 | 0.984 |
| Single model | 30.0% | 0.4900 | 1.204 |
| Uninformed 50% reference | 50.0% | 0.2500 | 0.693 |

Both forecasts leaned the wrong way. One resolved question is not evidence about accuracy or calibration; the matched resolved denominator for the original pilot remains as stated below.

Combined estimated cost was **$7.325915**. The maximum combined cost for an original assignment plus its repair check was **$1.074875**. Both remain below the original $150 total and $5 per question/method limits. Costs use the application's conservative pricing catalog and are estimates, not provider invoices.

## Release decision and limits

Keep the new profile opt-in. The original operational pilot did not pass, the three-track comparator still needs structured-output repair, and one successful new-method run is insufficient to promote it to the default.

Evidence review also found that the model sometimes labels methodology or release-schedule quotations as supporting evidence and assigns evidence sections too broadly. The repair run separately had numeric history/current conditions from the validated BLS adapter, but these semantic labels still need improvement and review before default use. A valid schema or verified quotation does not establish a sound interpretation.

Outcomes are still unknown. No human adjudications or accuracy scores were created. The matched resolved denominator is **0**. The ten original questions share six release events, including nested CPI thresholds. Future scoring must retain the correlation warning, coverage denominator, failures, and abstentions. Historical intraday macro requests currently withhold unless publication-time evidence can be established; date vintages alone are insufficient.

## Verification and retained evidence

- Python: **726 tests passed**; Ruff and mypy passed (102 source files).
- Frontend: ESLint, TypeScript, and the production build passed.
- Browser: **17 tests passed** against the isolated mock API and background worker, including both private-release fixtures.
- Dependency audit: **0 vulnerabilities**, including development dependencies. Dependency maintenance is in its own commit.
- Migration: all **231 pre-existing forecast-version rows are unchanged**; SQLite integrity and foreign-key checks passed.
- Browser setup initially exposed a build-time API rewrite pointing at the main local API. Those synthetic/demo executions cost $0. The unlaunched browser cohort was backed up and removed. Runtime proxy routing and explicit mock-provider/worker preflight checks now prevent recurrence.

Full run responses, original and corrected summaries, frozen-manifest receipts, repair results, independent aggregation/cost verification, backups, and a live screenshot are retained under `data/local/pilots/personal-v1-20260904/`. The original pre-migration backup is `data/local/backups/pre-personal-v1-20260904T214300Z.sqlite3`.

See [the pilot protocol](PERSONAL_V1_PILOT_PROTOCOL.md) for contracts and release dates, and [the implementation guide](RELIABLE_PERSONAL_V1.md) for the workflow, API, and limitations.
