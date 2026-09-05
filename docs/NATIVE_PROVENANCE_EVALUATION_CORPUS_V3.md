# Native-Provenance Evaluation Corpus V3

V3 is an acquisition and machine-screening boundary. It does not create an
`EvaluationRelease`, freeze a corpus, run forecasts, calculate scores, or make
a claim about forecasting quality or calibration.

## Purpose and status

The earlier V2 restoration kept 30 terminal provenance failures intact. Those
failures remain failures: V3 neither retries nor reinterprets them. V3 creates
a separate, private, content-addressed workspace for sources whose durable
historical records contain a timestamped binary question, resolution terms, and
final settlement metadata.

The active adapter is the public Kalshi historical-market API. Each accepted
candidate records the exact market title, published rules, native `created_time`,
native `settlement_ts`, market/event identity, source URL, source host, raw
record hash, canonical projected-text hash, and retrieval observation. The
native creation timestamp establishes the candidate's pre-outcome origin; local
retrieval time is audit-only and is never substituted for it.

Metaculus metadata and ForecastBench data are catalogued as possible source
families but are not accepted by this adapter without an accessible durable
pre-outcome origin record. A later dataset snapshot alone is not proof that a
question's terms existed before its outcome.

## Blinding and source records

The workspace keeps the following records separate:

- `records/blinded_candidates.jsonl` has the question, contract, cutoff,
  grouping, and source provenance but no outcome.
- `sealed/provisional_outcomes.jsonl` has the observed settlement, outcome time,
  and resolution source. It is not a forecasting-worker input.
- `sealed/raw_market_records/<sha256>` and sealed market-list payloads retain
  original public source responses with content-addressed filenames.
- `records/evidence_packets.jsonl` and `records/evidence_documents.jsonl`
  provide the native cutoff-safe source packet. A document is accepted only when
  its source-availability timestamp matches the native origin timestamp.
- `records/provenance_failures.jsonl` retains rejected or incomplete records
  with normalized reasons.

All locators are relative to the private external workspace. Raw responses,
sealed outcomes, and private paths are never committed to Git.

## Machine-complete is not evaluation-ready

`native_provenance_ready_pending_human_review` means only that automated checks
found a binary contract, native origin timestamp, final settlement record,
content hashes, source-use metadata, and a proposed event-family/leakage group.
It does not mean that the question is independently reviewed, the outcome is
adjudicated, licensing is settled, or the source is certified for redistribution.

The V2 preflight runs read-only against the blinded candidate and packet
records. It intentionally remains `release_ready: false` until the separate
review and adjudication process exists. The native sealed-input preflight
validates typed question and outcome source manifests in memory only; it creates
no review artifact and does not freeze a release.

## Deterministic grouping and provisional split

Candidates are deduplicated by native market and event identity. Event-family
and leakage-group IDs derive from the exchange event identifier, so related
markets cannot cross a provisional split. The fixed V3 seed assigns groups using
only candidate and grouping metadata, never outcomes, to targets of 60
development, 40 validation, 100 test, and 60 reserves. If those exact targets
cannot be met without group leakage, the tool reports the shortfall instead of
manufacturing an assignment.

## Running the acquisition safely

`scripts/acquire_native_provenance_candidates_v3.py` refuses an in-repository
workspace, dirty or mismatched source revision, missing locks, configured live
forecast providers, credential environment variables, and database access. It
uses only unauthenticated public HTTPS API requests. It creates no ForecastRun,
EvaluationRelease, experiment, review artifact, provider ledger, score, or
calibration result.

The tool is resumable: raw payloads and records are atomically written under
content-addressed paths, candidate IDs are stable, and a completed workspace can
be verified with `--verify-only` without network access. It will not lower the
minimum 200 native-provenance-ready threshold.

## First acquisition receipt

The first V3 public-source pass produced 260 machine-complete candidates from
the active native historical-market adapter: 60 provisional development, 40
validation, 100 test, and 60 reserve candidates. The read-only native sealed
input preflight passed. The ordinary V2 release preflight remains non-ready by
design because there are no review or adjudication artifacts. The sanitized
receipt is tracked at
[`artifacts/native_provenance_corpus_v3/summary.json`](../artifacts/native_provenance_corpus_v3/summary.json);
raw source responses and sealed provisional outcomes remain outside Git.

## Known limits

The corpus is still awaiting independent human question review, outcome
adjudication, licensing review, event-family review, and leakage review. Native
platform timestamps and historical APIs reduce an archive dependency but do not
eliminate the need to review terms, source rights, source durability, or possible
model-pretraining leakage. The latest live operational acceptance remains 3/5
FAIL. No certified evaluation corpus, frozen historical-evidence release,
forecasting-quality result, or calibration result exists.
