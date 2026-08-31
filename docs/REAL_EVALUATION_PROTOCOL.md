# ForecastLab real evaluation dataset protocol

Status: infrastructure protocol for assembling resolved historical datasets and running controlled measurements. Creating or running an experiment does not, by itself, support a forecasting-quality claim.

This document complements [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md). Synthetic fixtures remain useful for software verification, but they cannot establish forecasting accuracy, calibration, or profile superiority. Real evaluation requires auditable questions with outcomes that were unknown at the recorded forecast date and are known at import time.

## Dataset unit and identity

An `EvaluationDataset` is one versioned release with:

- `id`
- `name`
- `version`
- `hash`
- `description`
- `provenance`
- `status`
- `created_at`
- `frozen_at`
- `question_count`

`UNIQUE(name, version)` prevents silent replacement of a declared release. The SHA-256 `hash` covers the schema version, normalized dataset metadata, and canonical questions. Question order does not change the hash. Any content or provenance correction under an existing name and version is rejected; the correction requires a new version.

An `EvaluationQuestion` stores:

- `dataset_id`
- `question`
- a canonical binary `resolution_contract`
- `forecast_date`
- `resolution_date`
- `outcome`
- `resolution_source`
- `domain`
- optional `category` taxonomy

The resolution contract contains distinct `yes_condition` and `no_condition` values, the authoritative resolver, the resolution source and date, and the binary forecast type. The final outcome must be exactly `0` or `1`.

## Lifecycle

The only forward lifecycle is:

`draft` → `reviewed` → `frozen`

Import creates a `draft` only after every row passes structural validation. Review revalidates every stored question and refreshes the canonical hashes. Freeze is permitted only from `reviewed`, and only when the reviewed content, question count, and hashes remain unchanged.

Frozen dataset metadata and questions cannot be updated, appended, or deleted through the application persistence layer. Repeating review or freeze on an already frozen dataset is idempotent. A material correction requires a new dataset version and should be accompanied by a provenance change record.

## Required import fields

`POST /api/evaluation/datasets/import` accepts CSV or JSON. CSV uses `fixtures/benchmarks/evaluation_template.csv`. JSON may be an array of question objects, `{ "questions": [...] }`, `{ "rows": [...] }`, or one question object.

Every row requires:

- `question`
- `yes_condition`
- `no_condition`
- `forecast_date`
- `resolution_date`
- `outcome`
- `resolution_source`
- `authoritative_resolver`
- `domain`

Dataset name, version, description, and provenance are supplied as import metadata. Provenance is mandatory. Dates use ISO 8601.

The importer rejects the entire artifact if any row has a missing field, a non-binary outcome, a generic or duplicate resolution rule, a vague non-binary question, a forecast date at or after resolution, a future resolution date, or a duplicate question. It never persists a partial dataset.

## Dataset structural review standard

Before marking a dataset `reviewed`, the dataset review process must confirm:

1. The question was forecastable as of `forecast_date` and its outcome was not yet known.
2. The yes and no conditions are exhaustive enough to score consistently.
3. The named resolver has authority to determine the outcome.
4. The resolution source actually records the outcome under the frozen rule.
5. The final outcome follows the source and rule without looking at system performance.
6. The provenance describes where questions came from and how outcomes were verified.

Dataset rows remain the resolved-question source of truth. The separate `EvaluationRelease` layer stores licensing metadata, event-family and leakage-group membership, exclusions, split membership, versioned corrections, and immutable role-separated procedural AI review receipts without rewriting the underlying frozen datasets. Legacy opaque reviewer/adjudicator columns remain compatibility metadata and do not satisfy the production review gate.

## Required V1 splits

The initial real evaluation corpus uses three separately frozen datasets bound by one `EvaluationRelease`:

| Split | Questions | Permitted use |
| --- | ---: | --- |
| Development | 60 | Debugging, instrumentation, and future method development. |
| Validation | 40 | Select and freeze one candidate configuration. |
| Test | 100 | Run the frozen comparison for a bounded held-out result. |

Related questions, event families, revisions, and outcome-revealing sources must not cross splits. Split construction must preserve the temporal and leakage controls in [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md). Synthetic questions do not count toward these totals.

`private_v1_real_evaluation_release_v1` requires exactly 60 included development questions, 40 included validation questions, and 100 included test questions. A question ID, normalized question hash, or resolution-contract hash may appear only once. Event-family and leakage-group identifiers may occur multiple times within one split but never across splits. Excluded rows remain in the release audit with a reason and grouping metadata, but they are absent from both execution and scoring manifests.

## Release and preregistration lifecycle

An `EvaluationRelease` follows `draft` → `reviewed` → `frozen` and binds the three frozen datasets to:

- one immutable release policy snapshot;
- release-question split, review, licensing, exclusion, and adjudication metadata;
- a blinded execution manifest;
- a separately typed sealed scoring manifest;
- a complete preregistration;
- independently computed manifest hashes and one aggregate release hash;
- one passed `QuestionReviewArtifact` and one passed `OutcomeAdjudicationArtifact` per included question.

Every included question requires two fresh, role-separated Codex receipts with disjoint typed inputs, distinct run IDs, frozen rubric and model/tool identities, source-only citations, immutable input/output hashes, and passed deterministic validation. A known source-license classification, source-use basis, explicit redistribution flag, and valid temporal ordering remain mandatory. The sealed scoring manifest derives its outcome only from the passed adjudication receipt. See [Procedural AI Review Gate V1](PROCEDURAL_AI_REVIEW_GATE_V1.md).

Frozen releases and their membership rows are immutable. Repeating review or freeze against identical content is idempotent. A correction requires a new release version, a link to the prior frozen release, and a nonempty correction summary. ForecastLab provides structural blinding through separate DTOs and service boundaries; it does not claim cryptographic secrecy in the local application.

The preregistration freezes dataset, profile, prompt, source-code, dependency-lock, provider/model, budget, evidence-cutoff, metric, paired-comparison, bootstrap, calibration-reporting, exclusion, split-use, no-tuning, and one-shot-test identities before any run is assigned. See [Real Evaluation Preregistration V1](REAL_EVALUATION_PREREGISTRATION_V1.md).

## Frozen historical-evidence release

Every production release must be paired with one frozen `HistoricalEvidenceRelease` governed by `private_v1_historical_evidence_release_v1`. It covers every included release question exactly once at that question's forecast-date cutoff. A packet is either `ready`, with verified pre-cutoff content-addressed documents, or `no_eligible_evidence`, with a reviewed missingness audit. Missing evidence never removes a question from the denominator or authorizes an imputed probability.

Accepted web evidence requires a verified final Wayback capture at or before cutoff with a matching archived original. The alternative path is an explicitly registered immutable-version adapter with independent pre-cutoff availability proof. Retrieval dates, current-page fallbacks, search snippets, post-cutoff captures/publications, archive mismatches, and unregistered adapters are rejected.

The evidence execution manifest is structurally blinded from outcomes and scoring data. The separate audit manifest retains collection/review, licensing, rejected-candidate, missingness, correction, and redistribution metadata. Exact bytes and extracted text live in an external content-addressed bundle and are verified before any task is queued. See [Historical Evidence Release V1](HISTORICAL_EVIDENCE_RELEASE_V1.md).

## API

- `GET /api/evaluation/datasets` lists all lifecycle states and the controlled comparison profiles.
- `POST /api/evaluation/datasets/import` validates and creates a draft CSV or JSON release.
- `POST /api/evaluation/datasets/{id}/review` performs the review transition.
- `POST /api/evaluation/datasets/{id}/freeze` verifies and freezes a reviewed release.
- `GET /api/evaluation/datasets/{id}` returns metadata, questions, contracts, dates, outcomes, and sources.
- `GET /api/evaluation/datasets/template.csv` returns the blank import template.
- `GET /api/evaluation/releases` lists release lifecycle and audit identities.
- `POST /api/evaluation/releases` creates a draft release over three frozen datasets.
- `POST /api/evaluation/releases/{id}/review` runs deterministic policy validation.
- `POST /api/evaluation/releases/{id}/freeze` freezes the reviewed release and manifests.
- `GET /api/evaluation/releases/{id}` returns the release audit, including exclusions and hashes.
- `GET /api/evaluation/releases/{id}/execution-manifest` returns only the blinded worker DTO.
- `POST /api/evaluation/releases/{id}/question-review-artifacts` records one externally produced, outcome-blind Codex receipt.
- `POST /api/evaluation/releases/{id}/outcome-adjudication-artifacts` records one externally produced, question-review-blind Codex receipt after reserve ordering is frozen.
- `GET /api/evaluation/releases/{id}/review-artifacts` returns the immutable procedural audit.
- `GET /api/evaluation/evidence-releases` lists historical-evidence release lifecycle and hashes.
- `POST /api/evaluation/evidence-releases` imports a typed draft release and packet/document audit.
- `POST /api/evaluation/evidence-releases/{id}/review` verifies policy, manifests, and external bundle.
- `POST /api/evaluation/evidence-releases/{id}/freeze` freezes a reviewed release after revalidation.
- `POST /api/evaluation/evidence-releases/{id}/verify` runs the offline bundle verifier.
- `GET /api/evaluation/evidence-releases/{id}/execution-manifest` returns only accepted worker-safe evidence identities.

There is deliberately no general forecast-execution endpoint for the sealed scoring manifest.

## Controlled experiment runner

For production real evaluation, `POST /api/forecast-experiments` requires a frozen `EvaluationRelease`, a matching frozen `HistoricalEvidenceRelease`, a verified external bundle, and an explicitly permitted split. A missing, mismatched, changed, or incomplete evidence release creates zero tasks. Direct dataset-only creation remains available only for the existing synthetic software-verification path. The runner creates one `ForecastExperimentRun` for every included blinded question/profile pair. The controlled profile set is fixed to:

- `single_model_forecaster_v1`
- `three_track_forecaster`
- `graph_forecaster_v1`

Before any run is queued, `ForecastExperiment` stores one canonical, hash-protected configuration containing the evaluation release, historical-evidence release, bundle and preregistration identities, blinded question/evidence snapshots, profile source and effective definitions, common budget ceiling, provider and model metadata, prompt bundle and hashes, pricing catalog, evidence cutoffs, and code/dependency identity. Secret values and outcomes are never included. These input fields are immutable after creation. Execution reconstructs profiles, prompts, execution contexts, and the question-scoped offline evidence provider from the frozen record rather than mutable source files, network retrieval, or later Settings changes.

The forecasting executor receives a `BlindedEvaluationQuestion`, a type that cannot represent outcome or scoring fields. Only after the forecast is terminal does the scoring service join the outcome by evaluation-question identity from the sealed scoring manifest. Every assigned cell persists its status in `ForecastExperimentRun`. A successful or partial cell stores its probability, resolved outcome, Brier score, log loss, cost, latency, evidence coverage, and completion state in `ForecastExperimentResult`. A failure retains its error and operational measurements but receives no invented probability or forecast score. Job retries retain the same cell and forecast-run identities.

Read APIs are:

- `GET /api/forecast-experiments` for experiment progress;
- `GET /api/forecast-experiments/{id}` for assignments and frozen execution metadata;
- `GET /api/forecast-experiments/{id}/report` for the profile comparison table and question-level measurements.

The report presents measurements only. It performs no ranking, hypothesis test, or superiority claim.

## Claim boundary and limitations

These tables and the controlled runner are intentionally separate from the synthetic `BenchmarkDataset` workflow. This repository contains templates and generated test factories only; it does not contain or certify a 200-question real corpus or real historical-evidence bundle, freeze a production release pair, execute a real experiment, calculate a quality result, or establish calibration. Release validation proves that required metadata, structural leakage controls, temporal proof, and bundle hashes are present and internally consistent. It cannot prove that human grouping, licensing assertions, adjudication, representativeness, missingness review, or evidence relevance are substantively correct.
