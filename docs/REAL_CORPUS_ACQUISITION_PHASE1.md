# Real Corpus Acquisition Phase 1

Status: acquisition infrastructure and machine screening only. No candidate produced by this phase is reviewed, independently adjudicated, certified, frozen, executable as a real evaluation release, or evidence of forecasting quality.

The latest bounded live operational acceptance remains a hard 3-of-5 failure. Forecasting quality and calibration remain unestablished.

## Purpose

`private_v1_candidate_acquisition_v1` builds a resumable private queue of real, already-resolved binary questions for later independent human review. It keeps source payloads, provisional outcomes, historical document bytes, and private logs outside Git in a caller-selected workspace. The tracked repository receives only the acquisition tool, tests, a synthetic shape template, and a sanitized aggregate receipt.

The first public adapter uses Manifold's documented unauthenticated API for resolved binary markets. The source's own documentation makes permitted use dependent on context, so this phase records `unknown_pending_human_review`, disallows redistribution by default, and sends every candidate to a licensing queue. It makes no legal conclusion.

## Fail-closed execution boundary

`scripts/acquire_real_evaluation_candidates.py` requires:

- an exact clean source SHA;
- the frozen Python and frontend locks;
- a private workspace outside the repository;
- no OpenAI, Tavily, or other live ForecastLab provider configuration;
- no credential-bearing environment variables;
- no configured ForecastLab database.

The command has acquisition/resume and `--verify-only` modes only. It has no release review, release freeze, forecast execution, experiment, scoring, or calibration path. Public requests are unauthenticated GETs with bounded response sizes. Raw responses use relative content-addressed locators and atomic writes.

## Candidate and screening contract

Candidates must be public, resolved YES/NO records with a binary question, at least a seven-day forecast window, objective resolution signals, a published resolution-criteria description, and valid timestamp order. Deterministic screening rejects personal, subjective, malformed, unresolved, nonbinary, future-dated, and exact-duplicate records. A source platform's pre-outcome creation timestamp is retained as provisional origin proof; it still requires human confirmation.

Each blinded record contains question and contract text, dates, resolver identity, origin proof, domain/category, deterministic hashes, proposed event/leakage groups, and source-use metadata. The provisional outcome, outcome-known timestamp, and resolution record remain in a separate sealed file. Neither the tool nor Codex is a human reviewer or independent outcome adjudicator.

`machine_eligible_pending_human_review` means only that deterministic structure is complete, no cutoff violation or exact duplicate was detected, a ready or explicit no-evidence packet exists, and all human gates remain pending. It does not mean the question is suitable for a certified benchmark.

## Historical evidence

For each candidate the tool checks the origin and any published source links, up to a configured bound, through Wayback's public availability metadata at or before `forecast_date`. A document is accepted only when:

- the capture timestamp is at or before cutoff;
- the archived original matches the requested canonical URL;
- the final response remains on Wayback rather than escaping to current live content;
- the body is accessible and nonempty;
- exact bytes and extracted text pass content-address verification.

Retrieval time, live-page fallback, snippets, post-cutoff captures, archive mismatches, and inaccessible bodies never qualify. Rejected attempts remain in the audit. A reviewed `no_eligible_evidence` packet remains attached to the candidate rather than removing it or inventing evidence.

The acquisition target is three eligible pre-cutoff documents, two hosts, and one deterministic primary source where available. This is a target, not permission to weaken temporal or provenance rules. Shortfalls remain visible for human review.

## Leakage and provisional split

The tool hashes normalized questions, contracts, resolution events, and source identities; computes deterministic near-duplicate similarity; and proposes event-family and broader leakage-group identities using non-outcome metadata. Ambiguity is queued for human review.

A fixed seed assigns whole leakage groups, never individual outcomes, to provisional 60-development, 40-validation, and 100-test capacities. Remaining machine-complete groups become reserves. Outcomes are not read by the splitter, and no family or leakage group may cross provisional splits. If the exact split cannot fit without leakage, the tool reports the shortfall instead of manufacturing assignments.

## Human queues and hard stop

Separate files are generated for:

- question review;
- independent outcome adjudication;
- licensing review;
- event-family review;
- leakage-group review.

Reviewer and adjudicator identities remain blank. The phase stops after acquisition, machine screening, queue generation, and provisional splitting. It does not appoint reviewers, adjudicate outcomes, freeze releases, collect a certified evidence corpus, run forecasts, execute experiments, calculate scores, calibrate, merge, tag, publish, or deploy.

## Reproduction

From a clean committed source SHA:

```bash
uv run --frozen --no-sync python scripts/acquire_real_evaluation_candidates.py \
  --source-sha <exact-sha> \
  --workspace "$HOME/forecastlab-evaluation/private-v1-candidate-acquisition-v1" \
  --target 260

uv run --frozen --no-sync python scripts/acquire_real_evaluation_candidates.py \
  --source-sha <exact-sha> \
  --workspace "$HOME/forecastlab-evaluation/private-v1-candidate-acquisition-v1" \
  --target 260

uv run --frozen --no-sync python scripts/acquire_real_evaluation_candidates.py \
  --source-sha <exact-sha> \
  --workspace "$HOME/forecastlab-evaluation/private-v1-candidate-acquisition-v1" \
  --verify-only
```

The second acquisition command is a deterministic resume. Verification performs no network request and checks record, queue, blob, text, blinded/sealed, and source identities.

## Phase result

The completed Phase 1 acquisition produced 260 real, resolved binary candidates. All 260 passed machine completeness only and remain pending human question review, independent outcome adjudication, licensing review, event-family review, and leakage-group review. The outcome-independent provisional allocation contains 60 development, 40 validation, and 100 test candidates, with 60 reserves and no detected cross-split event-family or leakage-group assignment.

Historical evidence coverage is a material limitation:

- 30 candidates have a ready evidence packet and 230 have an explicit `no_eligible_evidence` packet;
- 35 documents were accepted, representing 26 distinct content blobs and 21 primary-source documents;
- zero candidates have three eligible documents;
- zero candidates have eligible documents from two hosts;
- all 35 accepted documents use verified pre-cutoff snapshot time rather than retrieval time;
- the corpus is concentrated in 2026, with 138 `other` and 60 `sports` candidates, and therefore is not representative evidence for evaluation quality.

The canonical external workspace manifest hash is `e224766217bf50c3cf2187738dfd55734e33de9b0bc35e529b0be502eef892ed`; the canonical summary hash is `cb77a1e347357f893fc1c2302b1ecf3f86a488845d735ad38febd44e045c9267`. Verification-only mode confirmed 260 records, content-address integrity, queue hashes, blinded/sealed separation, zero suspected secrets, zero network requests, and zero provider calls.

The sanitized aggregate receipt is tracked at `artifacts/real_corpus_acquisition_phase1/summary.json`. Raw records, provisional outcomes, downloaded bytes, extracted text, and private logs remain external. No reviewer or adjudicator was appointed, no release was reviewed or frozen, and no forecast, experiment, score, calibration calculation, OpenAI call, Tavily call, or paid-provider call occurred. Any evidence, representativeness, licensing, or independent-review shortfall is preserved rather than relaxed.
