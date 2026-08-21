# ForecastLab forecasting failure taxonomy

Status: required classification system for V1 forecast and experiment reviews.

This taxonomy gives ForecastLab a consistent way to record why a forecast, track, evidence packet, aggregation, or experiment failed. It is not a list of excuses for an incorrect outcome. A forecast may be well formed and still be wrong, and a forecast may resolve correctly while containing serious methodological failures.

The intended method is defined in [V1 Method Specification](V1_METHOD_SPEC.md). Evaluation validity and adoption decisions are governed by [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md) and [Experiment Decision Rules](EXPERIMENT_DECISION_RULES.md).

## Classification rules

- Record the earliest identifiable cause, not only the final symptom.
- Assign multiple codes when failures are materially distinct.
- Distinguish a detected-and-contained failure from one that affected the final probability.
- Do not infer a failure solely because the outcome was surprising.
- Do not clear a failure solely because the forecast happened to be correct.
- Preserve failures on partial and failed runs; do not remove them from evaluation records.

Every failure record must include:

- failure code and category;
- description of the observed condition;
- detection method and evidence;
- affected question, track, node, evidence item, run, or experiment;
- forecast stage and timestamp;
- effect on validity, probability, cost, or completion;
- prevention or corrective control;
- disposition: contained, partial, invalid forecast, invalid experiment, or follow-up required.

## Question failures

| Code | Failure | Description | Detection method | Prevention method |
| --- | --- | --- | --- | --- |
| Q-01 | Ambiguous resolution | The contract permits more than one reasonable scoring interpretation or leaves a material boundary undefined. | Independent contract review finds conflicting outcomes for the same hypothetical case; ambiguity notes do not resolve the conflict. | Require explicit yes, no, boundary, revision, rounding, ambiguity, and cancellation rules before forecasting. |
| Q-02 | Bad deadline | The deadline is missing, uses the wrong event time, lacks a usable time zone, occurs after the intended decision point, or conflicts with the resolver's release schedule. | Compare the contract deadline with the original question, authoritative release calendar, and forecast cutoff. | Normalize date and time zone, verify release timing, and reject contracts whose forecast and resolution chronology is invalid. |
| Q-03 | Wrong resolver | The named authoritative source cannot determine the outcome, is not authoritative for the stated geography or metric, or is likely to disappear without a valid fallback. | Resolver audit compares the contract with official ownership, record definitions, and availability. | Require a named authoritative record plus ordered fallback sources and resolver-risk notes. |
| Q-04 | Unclear outcome | Yes and no are not mutually exclusive and collectively exhaustive, or the event cannot be translated into a binary result. | Contract validator produces cases where both outcomes are true, both are false, or the result is unscorable. | Rewrite the outcome rules, add a cancellation disposition, or reject the question as outside V1's binary scope. |

## Research failures

| Code | Failure | Description | Detection method | Prevention method |
| --- | --- | --- | --- | --- |
| R-01 | Poor source selection | Research relies on low-authority, derivative, promotional, or irrelevant sources when better primary evidence is available. | Source-quality audit compares selected sources with the preferred evidence declared for the node and checks whether primary records were omitted. | Use pre-registered source preferences, rank official records first, and require an explanation when a weaker source is used. |
| R-02 | Missing evidence | A material factual driver or subforecast has no eligible supporting evidence, or important available contradicting evidence was not collected. | Claim-to-evidence audit finds an unsupported driver; coverage review finds a high-importance graph node with no evidence or explicit unknown status. | Require claim mapping, node coverage checks, and an explicit missing-evidence state before track submission. |
| R-03 | Duplicated sources | Multiple citations repeat the same underlying report, wire story, dataset, or upstream claim and are treated as independent corroboration. | Compare canonical URLs, content hashes, excerpts, publishers, citations, and upstream attribution. | Deduplicate by content and provenance, group derivatives under their common source, and disclose dependence. |
| R-04 | Outdated information | Evidence was available before the cutoff but no longer represented the relevant current state, or a newer eligible official update was ignored. | Compare publication dates, update cadence, superseding releases, and forecast cutoff; inspect whether the latest eligible version was used. | Define freshness expectations per node, retrieve versioned official records, and flag superseded evidence. |

## Reasoning failures

| Code | Failure | Description | Detection method | Prevention method |
| --- | --- | --- | --- | --- |
| G-01 | Wrong prior | The starting probability uses an irrelevant reference class, misstates its frequency, or assigns unjustified precision. | Recompute the cited base rate; audit reference-class inclusion criteria, sample size, and differences from the target case. | Require source-backed priors, explicit reference-class rationale, sensitivity to alternative classes, and a weak-prior label when evidence is poor. |
| G-02 | Overconfidence | The probability is too extreme for the evidence quality, unresolved uncertainty, resolver risk, or historical performance. | Reliability analysis, extreme-error review, and comparison of probability extremity with evidence quality and unresolved uncertainties. | Use bounded outputs, explicit uncertainty review, skeptic challenge, and only validated calibration or shrinkage rules. |
| G-03 | Narrative bias | A coherent story dominates the estimate despite weak base rates, missing alternatives, or contradictory evidence. | Review whether the reasoning depends on one causal story and whether opposing scenarios were generated and scored. | Separate decomposition from prose, require opposing scenarios, and make code calculate the final probability from structured inputs. |
| G-04 | Ignoring base rates | The forecast does not identify or use an available relevant reference class, or current evidence overwhelms it without justification. | Base-rate track or graph-node audit finds a missing applicable prior or an undocumented departure from it. | Make reference-class review mandatory and require the calculation trace to show the prior and update. |
| G-05 | Confirmation bias | Research seeks or retains mainly evidence that supports an early leaning and discounts credible contrary information. | Compare search plans, retained evidence, rejected sources, and counterarguments across directions; inspect skeptic findings. | Blind tracks to one another, require disconfirming searches and counterarguments, and audit rejection reasons. |

## Aggregation failures

| Code | Failure | Description | Detection method | Prevention method |
| --- | --- | --- | --- | --- |
| A-01 | Correlated forecasts treated as independent | Track or node estimates share model context, evidence, upstream facts, or causal drivers but receive full independent weight. | Dependency graph and evidence-overlap audit; compare shared citations, content hashes, common causes, prompts, and model state. | Declare dependencies, group or condition related contributions with a frozen code-owned rule, and test against a simpler baseline. |
| A-02 | Bad weighting | Weights are arbitrary, outcome-aware, unstable, inconsistent with the frozen profile, or place excessive influence on a weak or failed input. | Reproduce the calculation from stored inputs; compare effective weights with the pre-registered method and run sensitivity checks. | Version and freeze weighting rules, expose all contributions, constrain ranges, and change weights only through a new experiment. |
| A-03 | Poor calibration | Forecast probabilities systematically exceed or understate observed frequencies in held-out or prospective data. | Per-configuration reliability analysis with sample sizes and uncertainty; review overconfidence and underconfidence by probability range. | Do not claim calibration from small samples; fit any calibration transform on development data only, select on validation, freeze before test, and monitor prospectively. |

## Operational failures

| Code | Failure | Description | Detection method | Prevention method |
| --- | --- | --- | --- | --- |
| O-01 | Provider failures | Model, search, archive, or fetch providers time out, rate-limit, return invalid output, or become unavailable. | Provider-call ledger, status codes, timeout records, structured-output validation, retry history, and terminal run status. | Use explicit provider identity, bounded transient retries, permanent-error classification, durable attempt accounting, and fail-closed configuration. |
| O-02 | Budget exhaustion | The run reaches a model-call, search, fetch, token, cost, or wall-clock ceiling before the required method completes. | Budget ledger shows the exhausted limit, stage, reservations, and incomplete tracks or nodes. | Preflight expected workload, reserve before calls, use comparable ceilings, and surface partial or failed status rather than silently reducing the method. |
| O-03 | Evidence cutoff violation | A backtest uses evidence published, retrieved through an ineligible version, or otherwise unavailable after the forecast cutoff. | Publication and archive timestamp audit, final snapshot verification, content-version review, and cutoff-eligibility checks. | Enforce strict historical snapshots or proven immutable records, reject current-page fallback, and block the affected forecast from quality claims. |
| O-04 | Missing provenance | A forecast, claim, evidence item, prompt, profile, model, dataset, price, or code version cannot be traced to its source identity. | Reproduction audit finds a missing hash, timestamp, source, configuration field, or calculation input. | Freeze complete experiment identity, require claim-to-evidence mapping, and fail the validity gate when required provenance is absent. |

## Experiment and evaluation failures

These codes cover failures that can invalidate a comparison even when individual forecast runs completed.

| Code | Failure | Description | Detection method | Prevention method |
| --- | --- | --- | --- | --- |
| E-01 | Dataset leakage | Outcome information, duplicate event families, prompt examples, or later revisions cross development, validation, or test boundaries. | Split-manifest audit, similarity and event-family review, date checks, and provenance inspection. | Use temporal grouped splits, freeze manifests, and treat prompts and retrieval fixtures as data. |
| E-02 | Unfair baseline | Systems receive different questions, contracts, models, evidence eligibility, or resource ceilings without the difference being the registered treatment. | Compare frozen profile snapshots, execution contexts, budgets, question IDs, and paired coverage. | Run required baselines on the same questions and equalize total research ceilings for equal-budget comparisons. |
| E-03 | Outcome-aware exclusion | Failed or unfavorable questions are removed, relabeled, or moved after results are known. | Reconcile assigned questions with results, exclusions, dataset versions, job records, and decision timestamps. | Freeze exclusion rules, retain all assigned tasks, and require system-agnostic adjudication. |
| E-04 | Insufficient uncertainty evidence | A point estimate is presented as improvement without a valid paired interval or with an inadequate sample. | Report audit finds missing confidence interval, low paired coverage, or a primary interval that includes no improvement. | Pre-register sample size and uncertainty method, report inconclusive results, and prohibit adoption until the gate is met. |
| E-05 | Nonreproducible experiment | Code, prompts, profiles, model identity, pricing, evidence policy, dataset, or dependencies cannot be reconstructed. | Environment comparison or rerun audit finds missing or mismatched frozen identity. | Freeze all required artifacts and fail closed when a real experiment's execution identity differs. |

## Severity and scope

Each recorded failure receives one severity:

- **informational:** detected, contained, and unable to affect the probability or validity;
- **track-level:** invalidates or removes one track or graph node and may create a partial forecast;
- **forecast-level:** invalidates the probability for quality scoring;
- **experiment-level:** invalidates one or more comparisons or the entire improvement claim.

Severity is based on methodological effect, not whether the final outcome was correct.

## Disposition rules

- Question failures block forecasting until the contract is corrected and versioned.
- Confirmed cutoff violations or missing core provenance invalidate the affected forecast for quality claims.
- A failed independent track may produce a labeled partial forecast only when the frozen method permits it; it must not be imputed silently.
- Provider and budget failures remain in operational and cost denominators.
- Aggregation failures require a new method version and a new experiment after correction.
- Experiment-level failures produce an inconclusive result and rejection for adoption under the current experiment.

## Review cadence

Development reviews classify failures after every experiment. Validation and test reviews classify failures before winner interpretation and without changing frozen exclusion rules. Prospective reviews maintain a running failure register and publish the final distribution by category, configuration, and severity.

Recurring failures should become explicit prevention tests or protocol controls. A decline in one failure category must not be claimed as forecast improvement unless the required quality and uncertainty gates also pass.
