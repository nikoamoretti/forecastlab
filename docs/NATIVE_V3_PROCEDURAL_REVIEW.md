# Native V3 Procedural Review

This document describes the bounded private review controller for the native
provenance V3 candidate corpus. It is a procedural AI review step, not an
independent human review, a frozen evaluation release, a forecast, or a quality
or calibration result.

## Inputs and role separation

The controller projects each machine-complete candidate into two disjoint,
typed manifests under `private_v1_procedural_ai_review_v2`:

- `QuestionReviewManifest` contains the pre-outcome question, contract,
  grouping, and source provenance. Its schema cannot contain an outcome,
  resolution result, or scoring fields.
- `OutcomeAdjudicationManifest` contains the sealed outcome contract and
  resolution source. Its schema cannot contain question text, event-family,
  leakage-group, split, or question-review data.

Each model invocation is a fresh Cursor `ask` context in a separate external
workspace containing only its role's manifest. The controller persists only a
validated typed artifact or a sanitized terminal failure receipt. It never
persists raw model output, hidden reasoning, credentials, or provider headers.

## Execution boundary

`scripts/run_native_v3_procedural_review.py` refuses a dirty or mismatched
source revision, missing locks, configured ForecastLab provider/database
environment, an unavailable Cursor model catalog, an in-repository review
workspace, or a corpus that is not exactly the native 260-candidate sealed
input set. It records protected profile, prompt-source, and dependency-lock
hashes before any model invocation.

The primary external transport model is `gpt-5.3-codex-low-fast`. A transport
or schema failure is preserved as a terminal receipt; it is not mistaken for a
substantive review outcome. No ForecastLab OpenAI or Tavily provider, forecast,
experiment, score, calibration calculation, or release-freeze operation is
available through this controller.

## Interpreting the result

An artifact can pass the frozen procedural rubric only when every required
finding is source-cited, accepted or confirmed as appropriate, and has neither
uncertainty nor conflict. It remains procedurally AI-reviewed. Independent
human question review, outcome adjudication, licensing review, event-family
review, and leakage review are still required before a certified release could
be considered.

The tracked sanitized result, if one exists, contains aggregate counts and
hashes only. The external review workspace retains the immutable typed receipts.
The latest live operational acceptance remains **3/5 FAIL**. No certified
evaluation corpus, forecasting-quality result, or calibration result is implied.

## Authorized canary result

The first authorized canary used source commit `2871ba9bf03976b47eef2f5e4dcc6d08bb5a1b82`
after its exact CI run `33592695052` succeeded. It used the immutable V3 corpus
manifest `13b2e7ec2c926c1136854e002cc19b7e4dfcbc1c569d27f29210f7948365852a`
and its external campaign record `310a37401df2a79c892640be5699cc946b5fa2eb78693c53faca955e6f20a858`.

One fresh context for each role requested `gpt-5.3-codex-low-fast`. Both exited
with the sanitized terminal code `cursor_transport_exit_1` before emitting any
model output. The controller persisted neither raw output nor stderr; it retains
only the corresponding receipt hashes and stderr hashes in the private external
workspace. Therefore the canary has **zero** validated artifacts and **zero**
procedurally reviewed candidates. The remaining 259 candidates were not
launched. No escalation occurred because the failure was a transport exit before
schema or substantive review validation.

The result is tracked in
[`artifacts/native_v3_procedural_review/summary.json`](../artifacts/native_v3_procedural_review/summary.json).
Cursor transport usage and cost were not reported by the CLI. ForecastLab made
zero OpenAI or Tavily calls and incurred `$0.00` of ForecastLab provider spend.
This is a preserved external-transport blocker, not a result to reinterpret or
work around by silently restarting the campaign.
