# ForecastLab V1 Real Evaluation Protocol

## Purpose and claim boundary

Synthetic fixtures test software behavior. They do not measure forecasting quality. A quality evaluation must use resolved historical binary questions whose wording, resolution rules, forecast cutoff, authoritative source, and final outcome can be audited independently.

This framework stores and freezes those questions. It does not execute experiments yet, and importing a dataset does not support a claim that either ForecastLab profile is superior, calibrated, or production-ready.

## Dataset structure

An `EvaluationDataset` has a stable name and explicit version, description, provenance statement, content hash, creation time, freeze time, status, and question count. `UNIQUE(name, version)` identifies one declared release. The SHA-256 dataset hash covers the schema version, dataset metadata, and canonical question records; question order does not affect it.

Every `EvaluationQuestion` contains:

- original and normalized question text;
- a binary resolution contract with distinct yes and no conditions;
- an authoritative resolution source;
- forecast and resolution dates;
- a known outcome of exactly `0` or `1`;
- domain and category labels.

The importer rejects the entire artifact if any row is incomplete. It rejects missing or non-binary outcomes, missing conditions or resolution sources, invalid or future resolution dates, forecast dates at or after resolution, duplicate questions, and questions or rules that remain structurally ambiguous. No partial dataset is stored.

## Lifecycle and immutability

The lifecycle is:

`draft` → `reviewed` → `frozen`

The initial API accepts only complete imports and persists them as frozen datasets after validation. It does not expose draft editing or review endpoints. Once frozen, dataset metadata and questions cannot be updated, appended, or deleted through the ORM. A correction or material change requires a new version. Reimporting an identical name, version, and payload is idempotent; different content under an existing name and version is rejected.

## Required splits

The real V1 corpus must eventually contain three separately frozen datasets or manifests:

| Split | Required questions | Permitted use |
| --- | ---: | --- |
| Development | 60 | Diagnose errors and develop future candidates. |
| Validation | 40 | Select one final candidate and freeze it. |
| Test | 100 | Evaluate the frozen candidate once for a bounded final claim. |

Questions should be grouped by event family before splitting so near-duplicates and related resolutions cannot cross boundaries. Synthetic fixtures do not count toward these totals.

## Import format

`POST /api/evaluation/datasets/import` accepts an uploaded CSV or JSON file. CSV uses the blank template at `fixtures/benchmarks/real_evaluation_template.csv`. JSON may be an array, `{ "questions": [...] }`, `{ "rows": [...] }`, or one question object.

Required row fields are:

- `question`
- `yes_condition`
- `no_condition`
- `forecast_date`
- `resolution_date`
- `outcome`
- `resolution_source`

Optional row fields are `normalized_question`, `domain`, and `category`. Dataset name, version, description, and provenance are multipart metadata. Dates use ISO 8601. The resolution date must already have passed at import time.

## Provenance review

Before import, a reviewer must confirm that the resolution source is authoritative, the outcome follows the frozen rule, and the question was genuinely forecastable as of the forecast date. Dataset provenance should identify where the questions came from and how outcomes were verified. Source licensing, revisions, exclusions, event-family grouping, and corrections belong in an external manifest or review record until those fields are added to the schema.

## API surface

- `GET /api/evaluation/datasets` lists frozen real-evaluation releases.
- `POST /api/evaluation/datasets/import` validates and freezes one CSV or JSON release.
- `GET /api/evaluation/datasets/{id}` returns metadata, contracts, dates, outcomes, sources, and labels.

These tables are intentionally separate from the existing synthetic `BenchmarkDataset` and experiment tables. No evaluation experiment can select or execute an `EvaluationDataset` in this implementation.
