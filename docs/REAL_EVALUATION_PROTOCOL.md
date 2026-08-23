# ForecastLab real evaluation dataset protocol

Status: infrastructure protocol for assembling resolved historical datasets. It does not authorize experiment execution or support a forecasting-quality claim.

This document complements [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md). Synthetic fixtures remain useful for software verification, but they cannot establish forecasting accuracy, calibration, or profile superiority. Real evaluation requires independently auditable questions with outcomes that were unknown at the recorded forecast date and are known at import time.

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

## Review standard

Before marking a dataset `reviewed`, a reviewer must independently confirm:

1. The question was forecastable as of `forecast_date` and its outcome was not yet known.
2. The yes and no conditions are exhaustive enough to score consistently.
3. The named resolver has authority to determine the outcome.
4. The resolution source actually records the outcome under the frozen rule.
5. The final outcome follows the source and rule without looking at system performance.
6. The provenance describes where questions came from and how outcomes were verified.

This first schema does not store reviewer identity, licensing terms, event-family grouping, exclusions, split membership, or a correction log. Those must remain in a versioned external manifest until first-class fields are added.

## Required V1 splits

The initial real evaluation corpus uses separately frozen releases or deterministic manifests:

| Split | Questions | Permitted use |
| --- | ---: | --- |
| Development | 60 | Debugging, instrumentation, and future method development. |
| Validation | 40 | Select and freeze one candidate configuration. |
| Test | 100 | Run the frozen comparison for a bounded held-out result. |

Related questions, event families, revisions, and outcome-revealing sources must not cross splits. Split construction must preserve the temporal and leakage controls in [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md). Synthetic questions do not count toward these totals.

## API

- `GET /api/evaluation/datasets` lists all lifecycle states and reports that execution is not supported.
- `POST /api/evaluation/datasets/import` validates and creates a draft CSV or JSON release.
- `POST /api/evaluation/datasets/{id}/review` performs the review transition.
- `POST /api/evaluation/datasets/{id}/freeze` verifies and freezes a reviewed release.
- `GET /api/evaluation/datasets/{id}` returns metadata, questions, contracts, dates, outcomes, and sources.
- `GET /api/evaluation/datasets/template.csv` returns the blank import template.

## Claim boundary and limitations

These tables are intentionally separate from the synthetic `BenchmarkDataset` and experiment-runner tables. This implementation does not select, execute, or score real datasets. It does not populate real questions, and a frozen import alone is not evidence that the questions are representative, leakage-free, licensed, or correctly adjudicated. Those properties require the documented human review, external manifests, and the controlled experiment protocol.
