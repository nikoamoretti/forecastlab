# ForecastLab experiment decision rules

Status: mandatory V1 adoption gate for forecasting-method changes.

These rules determine whether a proposed change becomes part of the ForecastLab V1 method. They apply after an experiment has satisfied [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md). Passing software tests is necessary for implementation integrity but is not evidence of forecasting improvement.

## Required change proposal

Before validation or test execution, every proposed change must answer the following questions in a frozen experiment record.

### Hypothesis

What improvement is expected?

The hypothesis must name the mechanism, affected population, primary comparison, expected direction, and any expected tradeoff. “More capable,” “more intelligent,” or “better reasoning” is not a testable forecasting hypothesis.

### Implementation

What changed?

Identify the exact method, prompt, profile, graph rule, evidence rule, aggregation rule, calibration rule, model, or operational behavior changed. Record code, prompt, profile, and dependency identities. Bundled changes must be separated when their individual contribution cannot be inferred.

### Experiment

How will it be tested?

Name the dataset split, questions, baselines, budgets, provider and model, evidence cutoff, metrics, paired analysis, uncertainty method, and analysis population. State whether the run is development, validation, test, or prospective.

### Success criteria

What metric improves?

Name one primary forecast-quality endpoint, the minimum meaningful improvement if one is required, the confidence-interval rule, and reliability, cost, latency, evidence, and completion guardrails. Criteria must be written before results are visible.

### Cost

What additional resources are consumed?

Estimate and later report model calls, search calls, fetched documents, tokens, provider requests, total dollars, wall-clock time, and operational complexity. Include failed-attempt and failed-task cost.

## Pre-registration requirements

The experiment record must freeze:

- proposal ID and owner;
- hypothesis and mechanism;
- candidate and baseline identities;
- dataset, split, and question-family grouping;
- primary paired comparison and endpoint;
- secondary metrics;
- sample size and exclusion rules;
- bootstrap or other uncertainty settings;
- success threshold and regression guardrails;
- resource ceilings and pricing snapshot;
- evidence and cutoff policy;
- code, prompts, profiles, models, and dependencies;
- decision rule and date.

Changing any of these after seeing validation or test results invalidates the pre-registered decision. The altered method requires a new experiment identity.

## Validity gate

No adoption decision is made until all of these checks pass:

1. Every required baseline ran on the same eligible question set.
2. Contracts, cutoffs, outcomes, and system identities were frozen.
3. The candidate and equal-budget research baselines used the pre-registered resource ceilings.
4. Full, partial, failed, and excluded questions are accounted for.
5. Cutoff violations and mock or synthetic contamination are absent from real quality claims.
6. Primary analysis code and uncertainty settings match the freeze.
7. No test-set tuning, question substitution, or outcome-aware exclusion occurred.

A validity failure rejects the run as evidence. It does not establish that the candidate is good or bad.

## Acceptance rules

Keep a change only if all of the following are true:

- forecast quality improves;
- the confidence interval supports improvement;
- the cost increase is justified;
- reliability does not materially degrade.

For V1, this means:

1. The pre-registered primary paired forecast-quality metric favors the candidate on the held-out test set.
2. The pre-registered confidence interval excludes no improvement in the favorable direction.
3. The validation result and held-out test result agree on the direction of the primary effect.
4. Any increase in total cost or latency stays within the pre-registered limit and produces enough measured quality gain to justify it.
5. Completion rate, partial rate, failure rate, citation precision, unsupported claim rate, and cutoff compliance stay within their pre-registered guardrails.
6. Reliability diagnostics show no pre-defined material degradation. Sparse bins or small samples must be labeled insufficient rather than interpreted optimistically.
7. The result remains valid when failures and all assigned-question costs are included in operational reporting.

Qualitative report improvements may accompany an accepted quantitative result, but they cannot satisfy the gate.

This V1 rule does not define a cost-only or latency-only non-inferiority path. A proposal to accept unchanged quality for lower cost requires a separately approved protocol before the experiment runs.

## Rejection cases

Reject a change for adoption when:

- only qualitative improvement exists;
- benchmark size is insufficient;
- improvement disappears on held-out questions;
- cost grows faster than value.

Also reject for adoption when:

- the primary confidence interval includes no improvement;
- the candidate improves a secondary metric but misses the primary endpoint;
- the effect depends on dropping failed, partial, or unfavorable questions;
- reliability or completion materially degrades;
- evidence quality worsens beyond a guardrail;
- any real backtest contains an unresolved cutoff violation;
- the candidate was tuned on the test set;
- system identities or resource ceilings cannot be reproduced;
- an added agent, track, graph layer, or model call has no measurable incremental benefit;
- the result exists only on synthetic fixtures or anecdotal examples.

“Reject for adoption” does not always mean the mechanism is disproven. An underpowered or invalid experiment is recorded as inconclusive, but the practical decision is still not to adopt the change as an improvement.

## Decision outcomes

Every completed proposal receives exactly one decision.

### Accepted

All validity and acceptance rules pass. The accepted method version and effective date are recorded. Acceptance is limited to the tested scope and does not imply universal superiority or calibration.

### Rejected

The experiment is valid and the candidate misses a required criterion, reverses direction, violates a guardrail, or has an unjustified cost. The negative result remains in the research record.

### Inconclusive

The run is invalid, underpowered, interrupted, or too uncertain to answer the hypothesis. The change is rejected for adoption. A rerun requires a new pre-registration explaining what will change and why.

## Stage-specific decisions

### Development

Development results may be used to debug or generate candidates. They cannot accept a V1 method.

### Validation

Validation selects one final candidate using the pre-registered rule. Once selected, the candidate is frozen. Additional tuning creates a new validation cycle and cannot use the old test set as untouched evidence.

### Test

The held-out test is the adoption gate. It is run once for the frozen candidate. A failed test means the candidate is not adopted under this protocol.

### Prospective

Prospective evaluation checks whether the accepted retrospective result survives real-time forecasting. A material prospective reversal, cutoff breach, or operational failure triggers review and may suspend the method's accepted status.

## Cost and value review

Cost decisions use total assigned-question cost, including failures and retries. The decision record must show:

- candidate and baseline total cost;
- incremental cost per question;
- paired Brier improvement;
- accuracy improvement per dollar;
- latency difference;
- completion and failure differences;
- resource-ceiling utilization;
- any new maintenance or provider dependency.

Ratios are never reviewed without their component values and uncertainty. A tiny denominator, low coverage, or quality regression invalidates a favorable-looking ratio.

## Reliability review

Reliability is reviewed by configuration, not pooled across systems. The review includes sample size, probability-range coverage, reliability bins when available, overconfidence patterns, sharpness, and failure coverage. A chart display threshold is not an acceptance threshold and does not establish calibration.

## Decision record

The final record must contain:

- proposal and experiment IDs;
- hypothesis;
- exact implementation difference;
- dataset and split hashes;
- candidate and baseline identities;
- primary and secondary results;
- confidence intervals and sample sizes;
- full, partial, failed, and excluded counts;
- evidence-audit results;
- cost and latency analysis;
- validity-gate result;
- accepted, rejected, or inconclusive decision;
- rationale tied directly to the pre-registered rules;
- remaining limitations and required follow-up.

The record must not reinterpret an unfavorable result as a different successful hypothesis after the fact.

## Method removal

An accepted method may be suspended or removed when prospective evidence materially contradicts the original result, a leakage or provenance defect invalidates the evidence, reliability degrades outside the accepted scope, or operating cost exceeds the approved tradeoff. Removal preserves historical experiment records and creates a new method version; it does not rewrite past results.
