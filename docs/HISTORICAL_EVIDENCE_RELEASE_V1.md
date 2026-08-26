# Historical Evidence Release V1

Status: offline evaluation-integrity infrastructure. ForecastLab has not collected, certified, or frozen a real historical-evidence corpus and has not run a real evaluation experiment.

## Purpose and claim boundary

`private_v1_historical_evidence_release_v1` binds one immutable, cutoff-safe evidence corpus to one frozen `EvaluationRelease`. The release answers a narrow execution-integrity question: which exact document bytes and extracted text may a historical forecast use for each included evaluation question?

This boundary does not establish that the corpus is representative, complete, topically sufficient, correctly licensed, or useful for forecasting. It does not eliminate model-pretraining leakage. The latest bounded live operational acceptance remains a hard 3-of-5 failure, and this offline infrastructure does not reinterpret it.

## Lifecycle and identity

A `HistoricalEvidenceRelease` follows:

`draft` → `reviewed` → `frozen`

It is bound to exactly one already-frozen `EvaluationRelease` and its blinded execution-manifest hash. The release owns independently hashed execution, audit, and bundle manifests plus one aggregate release hash. `UNIQUE(name, version)` prevents replacement under an existing identity.

Review and freeze are deterministic and idempotent when all inputs are unchanged. A conflicting same-name/version request fails closed. A correction requires a new version, a link to the prior frozen evidence release, and a nonempty correction summary. Frozen release, packet, document, candidate, and linkage rows cannot be updated or deleted. Existing rows are never rewritten and migration `20260826_0028` fabricates no historical evidence.

## Complete packet coverage

Every included `EvaluationReleaseQuestion` must have exactly one packet with the same split and an evidence cutoff exactly equal to the question's forecast date. Excluded questions cannot have executable packets.

Packet states are:

- `ready`: one or more independently verified documents are linked and at least one reviewed accepted candidate identifies an included document.
- `no_eligible_evidence`: reviewed searches, candidate/archive checks, and normalized rejection reasons establish that no eligible document was accepted.

A no-evidence packet remains in the evaluation denominator. It returns zero frozen search hits and cannot remove or reclassify the question, create synthetic evidence, or impute a probability.

Collectors and reviewers use opaque identifiers and must be different. Candidate rows retain query, provider, canonical/source URLs, stable rank, acceptance or rejection status, archive-check status, and normalized rejection reason. Rejected candidates remain audit records and cannot link executable evidence.

## Accepted temporal proof

An accepted document must use one of two explicit bases:

1. `wayback_final_capture`: the verified final Wayback replay timestamp is at or before cutoff, the archived original canonicalizes to the intended URL, and the response never escaped to current live content.
2. `immutable_version`: an explicitly registered, capability-gated adapter independently proves that a named immutable source version was available at or before cutoff.

Retrieval time, a current page, a search snippet, a provider date hint, an unregistered adapter, an archive mismatch, or a post-cutoff capture/publication cannot qualify historical evidence. Unknown publication time is allowed when a verified snapshot or immutable version independently proves availability. Retrieval time is never copied into publication time.

## Content-addressed bundle

The portable bundle contains:

- `blobs/<content_sha256>` for exact fetched bytes;
- `text/<extracted_text_sha256>` for exact UTF-8 extracted text;
- a typed bundle manifest with relative content-addressed locators and release/document identities.

Identical bytes and extracted text are deduplicated by hash, and one document may support multiple packets. The verifier rejects absolute paths, path traversal, noncanonical locators, missing or changed bytes/text, length mismatches, non-UTF-8 extracted text, release-identity mismatch, and extra executable files under `blobs/` or `text/`.

Real non-redistributable bytes belong outside Git. Licensing metadata records source-license status, use basis, and redistribution permission; storing metadata does not grant redistribution rights. The offline verifier is available as `scripts/verify_historical_evidence_bundle.py` and accepts the manifest, bundle root, and expected release hash. It performs no network request.

## Execution and audit separation

The worker-facing `BlindedHistoricalEvidenceExecutionManifest` contains only release/question/split/cutoff identity, packet state, and accepted document provenance, hashes, and relative locators. Its strict schema cannot represent outcomes, resolution/scoring sources, adjudication, outcome-known timestamps, scores, or post-resolution evidence.

The separate audit manifest may contain collector/reviewer identities, licensing, accepted and rejected candidates, archive checks, missingness, redistribution metadata, and correction history. Neither manifest stores credentials or raw provider responses.

Changing a sealed outcome does not change the evidence execution manifest or historical-evidence release hash. Changing accepted evidence bytes, extracted text, temporal proof, metadata, packet linkage, or cutoff does.

## Offline execution provider

`FrozenEvidenceSearchProvider` and `FrozenEvidenceDocumentStore` are bound to the experiment, evaluation-release/hash, historical-evidence release/hash, split, question, and cutoff. Before assignments are queued, ForecastLab verifies the complete bundle and exact packet coverage.

During a production historical evaluation:

- no Tavily, Wayback, or HTTP fallback is available;
- frozen searches consume the profile's logical search allowance but have zero search cost and do not count as network provider requests;
- search ranks only the question's linked documents deterministically by token overlap, source class, and canonical URL;
- fetch rechecks content and text hashes and lengths;
- cross-question and cross-split document access fails closed;
- `no_eligible_evidence` yields zero hits and no imputation;
- the scoring manifest remains outside the worker-facing types.

Synthetic software-verification, demo, live, and operational-smoke workflows keep their existing provider paths. Direct dataset-only real evaluation remains forbidden.

## API and templates

The minimal release API is:

- `GET /api/evaluation/evidence-releases`
- `POST /api/evaluation/evidence-releases`
- `GET /api/evaluation/evidence-releases/{id}`
- `POST /api/evaluation/evidence-releases/{id}/review`
- `POST /api/evaluation/evidence-releases/{id}/freeze`
- `POST /api/evaluation/evidence-releases/{id}/verify`
- `GET /api/evaluation/evidence-releases/{id}/execution-manifest`

Blank, synthetic-only input shapes live in `fixtures/evaluation/historical_evidence_release_template.json` and `fixtures/evaluation/historical_evidence_candidates_template.csv`. They contain no real evaluation questions or source content.

## Remaining work

ForecastLab still requires independently curated and reviewed 60/40/100 real datasets, a legally reviewed and frozen real historical-evidence bundle, human verification of temporal proof and missingness audits, one genuinely frozen production evaluation/evidence pair, and the preregistered experiment. No forecasting-quality or calibration claim exists until those steps and the full evaluation protocol are completed.
