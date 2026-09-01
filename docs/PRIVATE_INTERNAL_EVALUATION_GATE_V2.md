# Private Internal Evaluation Gate V2

Status: offline evaluation-integrity infrastructure. This policy does not collect
questions, review a corpus, freeze a release, execute a forecast, score a result,
or establish forecasting quality or calibration.

`private_v1_real_evaluation_release_v2` and
`private_v1_procedural_ai_review_v2` are a versioned, private-internal successor
to the V1 procedural review policy. V1 rows, hashes, rubrics, artifacts, and
frozen snapshots remain valid under V1; V2 does not reinterpret them.

## What V2 keeps fail-closed

V2 retains the controls required for a defensible private internal review:

- an exact binary question and resolution contract;
- distinct 60/40/100 development, validation, and test membership;
- duplicate-question, contract, event-family, and leakage-group rejection across
  included splits;
- separate question-review and outcome-adjudication artifacts with distinct run
  identities and role-specific, structurally blinded DTOs;
- no outcome, scoring, probability, or post-resolution data in the question
  review input, and no question-review data in the outcome input;
- a source URL, title, publisher, source-availability timestamp, retrieval time,
  content hash, extracted-text hash, concise evidence note, and either a known
  publication date or an explicit unknown-date state for every V2 source;
- pre-outcome source availability for question review and resolution-to-
  outcome-known temporal ordering for adjudication;
- canonical input/output hashes, deterministic validation, immutable receipts,
  deterministic reserve ordering, and the one-shot test-split claim; and
- explicit preservation of missing evidence, uncertainty, conflict, leakage, and
  source-provenance failures.

V2 artifacts use `private_v1_question_review_rubric_v2` and
`private_v1_outcome_adjudication_rubric_v2`. Their source-provenance and temporal
findings are mandatory. A missing publisher, retrieval time, date-or-unknown
state, extracted-text hash, or evidence note fails before an artifact is stored.

## Licensing metadata is audit-only in V2

`source_license_status`, `source_use_basis`, and `redistribution_allowed` remain
stored and visible audit metadata. V2 does not fail a *private internal* release
solely because licensing is unknown, a use basis is absent, or redistribution is
false. This is not a legal conclusion, permission to redistribute source content,
or authorization for public reporting. Public release, sharing, or publication
requires a separate legal and human-review decision.

## Preflight scope and claim boundary

The V2 preflight only reports deterministic readiness blockers. It never changes
candidate records, creates review receipts, fills reviewer identities, freezes a
release, runs forecasts, scores results, or substitutes a no-evidence packet with
a probability. `no_eligible_evidence` remains an honest audited state.

Passing V2 means only that a release has met the versioned private-internal
integrity checks. It is not evidence of independent human review, forecasting
accuracy, calibration, representativeness, or profile superiority. The preserved
live operational acceptance result remains 3 of 5 failed.
