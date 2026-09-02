# V2 Provenance Restoration and Sealed Review Runner

This boundary prepares a candidate for a later private procedural review. It
does not create a `QuestionReviewArtifact` or `OutcomeAdjudicationArtifact`,
does not freeze an evaluation release, and does not authorize forecasting,
scoring, calibration, or a public claim.

## Restoration

`scripts/restore_v2_review_provenance.py` accepts only the frozen H017
evidence-ready allowlist. It re-fetches only a document's already-recorded
Wayback final-capture URL and verifies its final capture, original URL,
pre-cutoff timestamp, raw-content hash, and locally retained extracted-text
hash. It records title, publisher/origin host, current retrieval timestamp,
source-availability timestamp, temporal basis, hashes, source role, and an
evidence note in a private output directory outside the repository.

A current retrieval never establishes historical availability. A source that
redirects away from the archive, changes bytes, lacks a usable title, fails to
fetch, or has an invalid cutoff proof is retained as a failed source receipt.
The script never converts a provisional market-creation timestamp into an
independently verified pre-outcome origin proof, and it never treats a current
outcome page as proof that the result existed at the historical outcome time.

For each candidate, the output is either review-ready or a terminal provenance
failure with stable reasons. Missing provenance is not backfilled from a local
file timestamp, a search snippet, a current page, or licensing metadata.
Licensing and redistribution fields remain audit-only under private-V1 V2.

## Sealed role inputs

`forecastlab.sealed_review_runner` creates two separate typed input roots:

- `question_review` receives only `QuestionReviewManifest` values.
- `outcome_adjudication` receives only `OutcomeAdjudicationManifest` values.

On macOS the runner uses `sandbox-exec`. The sandbox denies the user home,
the rest of the campaign root, outbound networking, and all writes. It
re-allows only the selected role's manifest directory. The controller captures
an output receipt after process exit and appends it with exclusive file
creation; the sandboxed role cannot read or overwrite prior receipts.

The runner's negative tests prove that a role cannot read the other role's
manifests, repository files, campaign metadata, sealed scoring data, previous
receipts, or the network. A later actual review must use this boundary or an
equivalently enforced external runner. It must still record separate fresh role
identities and preserve the existing V2 rubric, manifest, and artifact
validation.

The runner accepts an explicit role command but deliberately does not turn its
output into a review artifact. Only an outer controller can validate a terminal
role result and append one receipt after sandbox exit. This restoration did not
materialize real candidate manifests or invoke a review command.

## Current limitation

The initial H017 evidence-ready tranche still requires independent review and
adjudication. The live operational acceptance remains 3/5 FAIL. Neither the
existence of this runner nor a successful source re-fetch establishes
forecasting quality, calibration, human independence, or readiness for a
frozen evaluation release.
