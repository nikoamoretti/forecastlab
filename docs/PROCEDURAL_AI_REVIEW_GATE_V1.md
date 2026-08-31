# Procedural AI Review Gate V1

Status: offline evaluation-integrity infrastructure. No corpus review, outcome adjudication, release freeze, experiment, score, or provider call was performed while adding this gate.

## Claim boundary

The only permitted release label is:

> Procedurally AI-reviewed private-V1 evaluation release

That label means the frozen release contains two fresh, role-separated Codex receipts per included question and passed the deterministic checks below. It does not mean human reviewed, independently validated, publication grade, accurate, calibrated, or suitable for a public quality claim.

## Two role-separated artifacts

`QuestionReviewArtifact` receives only an outcome-blind `QuestionReviewManifest`. The manifest contains the question, binary contract, forecast and resolution dates, resolver identity, event-family and leakage-group IDs, declared source-use metadata, and pre-outcome source records. Its type cannot represent an outcome, outcome-known timestamp, resolution result, score, forecast probability, or post-resolution adjudication.

`OutcomeAdjudicationArtifact` receives only an `OutcomeAdjudicationManifest`. It contains the contract conditions, resolution date, candidate binary result, outcome-known timestamp, and post-resolution source records. Its type cannot represent question-review output, normalized question text, split, event-family ID, leakage-group ID, reserve order, forecast probability, or score.

The two artifacts require distinct Codex run IDs. Each receipt freezes model provider, model ID and version, tool name and version, role, run ID, start/completion timestamps, rubric identity, canonical input hash, canonical output hash, decision, citations, and gate result. The application records externally produced receipts; recording a receipt does not itself call a model.

## Frozen rubrics and source grounding

The question role uses `private_v1_question_review_rubric_v1`. It requires source-cited findings for:

- binary contract;
- objective resolution;
- pre-outcome origin;
- authoritative resolver;
- event family;
- leakage group;
- licensing metadata.

The outcome role uses `private_v1_outcome_adjudication_rubric_v1`. It requires source-cited findings for:

- resolver authority;
- outcome-to-contract agreement;
- temporal order;
- source consistency.

Every citation must name a source ID present in that role's frozen input manifest. Hidden reasoning, external-knowledge citations, raw prompts, and provider response bodies are outside the persisted artifact contract. Missing required fields, disallowed role data, unknown citations, rubric drift, hash mismatch, stale receipts, licensing uncertainty, source conflict, outcome conflict, or any explicit uncertainty fails closed.

## Deterministic reserve order

`private_v1_deterministic_reserve_order_v1` freezes reserve ordering when the draft release is created, before any outcome-adjudication receipt can start. The order is derived from the fixed seed `20260831` and only question/contract identity, split, event family, leakage group, and inclusion metadata. Outcomes and scores are not inputs. Changing a sealed outcome therefore cannot change the reserve order.

Reserve ordering is an audit control, not a method for balancing outcomes or optimizing evaluation results.

## Release and experiment gates

A production release can review and freeze only when:

- split counts are exactly 60 development, 40 validation, and 100 test;
- the existing leakage, temporal, licensing, correction, profile, prompt, source, and lock checks pass;
- every included question has one passed question-review artifact and one passed outcome-adjudication artifact;
- all role run IDs are distinct;
- the aggregate artifact manifest and all constituent hashes match;
- the scoring manifest derives outcomes only from passed adjudication artifacts.

The test split is one shot. `EvaluationTestSplitExecution` is claimed before forecast tasks are created and has a database uniqueness constraint on release identity. Repeating the same claim is idempotent; a second experiment for the same release fails closed.

Uncertainty, disagreement, or a failed artifact does not remove a question, substitute a reserve, alter an outcome, or permit a partial release under the same version. A correction requires a new version and immutable lineage.

## Limitations

The gate proves procedural separation, typed data minimization, deterministic validation, and immutable receipts. It does not create independent human judgment. Model errors can agree across role-separated runs, source licensing assertions can still be wrong, local structural blinding is not cryptographic secrecy, and an administrator can access both stores. A real release still requires an actually executed, evidence-preserving review process and the separately frozen historical-evidence corpus. Forecasting quality and calibration remain unestablished, and the latest bounded live acceptance remains a hard 3-of-5 failure.
