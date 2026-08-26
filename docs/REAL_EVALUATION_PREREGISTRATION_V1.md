# Real Evaluation Preregistration V1

Status: offline evaluation-integrity infrastructure. No real corpus has been certified, no production release has been frozen, and no experiment or calibration analysis has been run.

## Purpose and claim boundary

`private_v1_real_evaluation_release_v1` freezes the identities, leakage controls, review metadata, licensing metadata, execution payload, scoring payload, and analysis plan required before ForecastLab may assign a real evaluation question. It does not collect questions, validate the truth of human annotations, construct historical evidence, execute forecasts, or establish forecasting quality.

The latest bounded live operational acceptance remains a hard 3-of-5 failure. This release boundary neither reinterprets nor repairs that result.

## Release lifecycle

An `EvaluationRelease` binds three distinct, already-frozen `EvaluationDataset` rows:

`draft` → `reviewed` → `frozen`

The production policy requires exactly:

| Split | Included questions | Permitted use |
| --- | ---: | --- |
| Development | 60 | Debugging, instrumentation, and future method development. |
| Validation | 40 | Select and freeze one candidate configuration. |
| Test | 100 | One-shot held-out measurement only. |

All questions from the three source datasets remain represented in release audit rows. An excluded row requires a predeclared reason, split, event-family ID, and leakage-group ID. It does not count toward totals and appears in neither execution nor scoring assignments.

A frozen release and its membership rows are immutable. Repeating review or freeze with identical content is idempotent. Any correction requires a new version, a link to the prior frozen release, and a correction summary. History is never overwritten.

## Leakage and review rules

For included questions, the release rejects:

- duplicate evaluation-question IDs;
- duplicate normalized-question hashes;
- duplicate resolution-contract hashes;
- an event-family ID used in more than one split;
- a leakage-group ID used in more than one split;
- a synthetic, fixture, demo, or mock-labeled source dataset.

Event-family grouping must keep revisions, repeated releases, related thresholds, company-period events, and materially coupled outcomes in one split. `leakage_group_id` supplies an additional conservative grouping boundary when different event families can reveal one another.

Every included question requires a nonempty event family and leakage group, an opaque reviewer ID, a different opaque outcome-adjudicator ID, a completed review time, and an adjudication-record SHA-256. Reviewer identifiers are internal opaque labels, not names or email addresses.

Every included row also requires a source-license status other than `unknown`, a source-use basis, and an explicit redistribution flag. Storing source metadata when redistribution is false does not grant permission to redistribute source content.

Temporal validation requires:

`forecast_date < resolution_date <= outcome_known_at`

and independently:

`forecast_date < outcome_known_at`

## Structural blinding

The release creates two independently typed and hashed manifests.

### Blinded execution manifest

The worker-facing `BlindedEvaluationQuestion` contains question identity, split, question text, frozen binary resolution contract, forecast and resolution dates, domain/category, generic authoritative resolver identity, evidence cutoff, and preregistration identity.

Its schema does not define outcome, post-resolution source, adjudication, outcome-known time, Brier score, log loss, or other scoring fields. Extra fields fail validation. Forecast construction therefore cannot receive a scoring object through this service boundary.

### Sealed scoring manifest

The separate scoring payload contains evaluation-question identity, binary outcome, post-resolution source, outcome-known time, adjudication-record hash, and scoring-contract hash. It is not exposed through a general execution API. The scoring service may join it only after the corresponding forecast is terminally persisted.

This is structural blinding, not encryption. A local database administrator can access both stored manifests.

## Preregistered identities and methods

Before assignments exist, `EvaluationPreregistration` freezes:

- release policy and version;
- all three dataset IDs and hashes;
- exact split sizes;
- controlled profile IDs, versions, hashes, complete source payloads, and per-profile ceilings;
- prompt versions and hashes;
- source commit, tracked-source hash, project hash, Python lock hash, and frontend lock hash;
- provider/model/search identities without credentials;
- evidence cutoff at `forecast_date`;
- Brier score as the primary metric and log loss as the secondary metric;
- completion, failure, cost, latency, and evidence coverage as operational metrics;
- paired comparison by evaluation-question identity;
- paired percentile bootstrap with 2,000 samples, seed `20260823`, and a 95% interval;
- a minimum sample of 20 for calibration reporting;
- no invented probability and no score for failed runs;
- frozen exclusions and split-permitted uses;
- a one-shot test rule and no-tuning rule;
- permitted claim language such as “observed difference” and prohibited unsupported language such as “winner,” “best,” “superior,” or “calibrated.”

Profile, prompt, source, tracked-tree, project, or lock drift fails release review/freeze or experiment recreation. Credentials are never part of the preregistration.

## Hashes and immutability

ForecastLab hashes canonical representations independently:

1. blinded execution manifest;
2. sealed scoring manifest;
3. preregistration;
4. aggregate release payload.

The aggregate release hash covers the policy snapshot, three dataset identities and hashes, every release-question audit row, and the three hashes above. Changing an outcome leaves the execution-manifest hash unchanged but changes the scoring-manifest and release hashes. Changing question or contract content changes execution and release hashes.

Frozen release fields, manifests, membership rows, and experiment configuration cannot be updated through the persistence layer. Conflicting content under an existing name/version fails closed rather than overwriting history.

## Experiment boundary

A production real-evaluation experiment must specify a frozen evaluation release, a matching frozen historical-evidence release, its verified external bundle, and one permitted split. Assignments come only from included entries in the blinded question manifest whose evidence packet has the exact forecast-date cutoff. Direct dataset-only experiment creation is rejected for production and remains available only to existing synthetic software-verification workflows.

The experiment snapshots evaluation release, historical-evidence release, bundle, and preregistration identities alongside the existing profile, prompt, pricing, budget, provider, source, and dependency configuration. Release, question/evidence manifest, bundle bytes, dataset, profile, prompt, source, or lock drift prevents execution before tasks are queued. Forecast construction uses a question-scoped offline evidence provider and cannot call current search, Wayback, or live-document fallback. After a terminal run, the scoring service joins the sealed outcome by exact evaluation-question identity. Failed runs, including reviewed no-evidence cases, retain operational measurements but receive neither an invented probability nor a forecast score.

## Remaining work

ForecastLab now has the offline historical-evidence release, bundle-verification, and worker-isolation boundary, but it still needs independently curated and reviewed 60/40/100 real datasets, a legally reviewed pre-cutoff historical evidence corpus, human-verified event/leakage grouping, missingness, and licensing, one genuinely frozen production release pair, and the preregistered evaluation itself. No real corpus was populated or frozen by this infrastructure task. Until those steps are complete, there is no real forecasting-quality result and no calibration claim.
