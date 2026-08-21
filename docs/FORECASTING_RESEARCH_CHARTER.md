# ForecastLab forecasting research charter

Status: governing internal specification for ForecastLab V1 research.

This charter governs methodology, experiment design, and claims about ForecastLab. It is not evidence that the current system is calibrated or superior to a baseline. In this document, **must** is a requirement, **should** is the default unless a written exception is approved, and **may** is optional.

## Mission

ForecastLab is a probabilistic forecasting system designed to produce calibrated, evidence-grounded predictions about future events.

Calibration is a research objective, not a current product claim. ForecastLab may be described as calibrated only after the evaluation requirements in [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md) are satisfied on held-out and prospective questions.

ForecastLab is not optimizing:

- eloquent reasoning;
- number of agents;
- length of reports;
- apparent intelligence;
- confidence.

ForecastLab is optimizing:

- forecast accuracy;
- calibration;
- evidence quality;
- reproducibility;
- cost efficiency;
- decision usefulness.

The primary research artifact is a probability tied to a frozen question, forecast time, and resolution contract. Explanations, evidence packets, and reports exist to make that probability auditable and useful. They are not substitutes for forecast quality.

## Scope and authority

This charter applies to:

- forecasting methods and aggregation rules;
- prompts, models, profiles, evidence policies, and research-track designs;
- benchmark and prospective experiments;
- acceptance or rejection of claimed improvements;
- internal and external performance claims.

The intended V1 system is specified in [V1 Method Specification](V1_METHOD_SPEC.md). Experiments must follow [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md) and [Experiment Decision Rules](EXPERIMENT_DECISION_RULES.md). Errors must use [Forecasting Failure Taxonomy](FORECASTING_FAILURE_TAXONOMY.md).

When an existing implementation differs from these documents, the difference is an open implementation gap. The implementation must not be described as conforming until the gap is closed and tested.

## Core principles

### 1. Probabilities over narratives

The output is a probability, not an explanation. Narrative quality is useful only when it improves auditability, evidence use, or decision usefulness. It does not count as forecast improvement by itself.

### 2. Evidence over intuition

Every factual driver must trace to stored evidence. A driver may be marked as an inference, but the underlying facts must still be cited. Unsupported factual claims are research failures, even when the final probability happens to be correct.

### 3. Independence before aggregation

Independent forecasts must not anchor each other. Research tracks may share the resolution contract and pre-registered task definition, but they must not see one another's probabilities, conclusions, or reasoning before submitting their own outputs. Any shared evidence pool or shared upstream model context must be declared because it can create dependence.

### 4. Baselines before complexity

Every new architecture must beat simpler alternatives on the same questions under a fair, frozen comparison. More agents, more tokens, more evidence, or a more elaborate graph are costs, not accomplishments. Complexity is accepted only when measured benefit justifies it.

### 5. Reproducibility before improvement

Every experiment must freeze:

- code;
- prompts;
- models;
- profiles;
- pricing;
- evidence policy;
- dataset.

It must also retain the forecast cutoff, resolution contract, provider identity, resource ceilings, and experiment configuration needed to reproduce or audit the run. An experiment with incomplete identity is invalid for an improvement claim.

### 6. Paired evidence before broad claims

Systems must be compared at the question level on the same eligible questions. Aggregate scores without paired coverage, uncertainty intervals, and failure accounting are insufficient.

### 7. Reliability and cost are part of quality

A system that is slightly more accurate but materially less reliable, much slower, or disproportionately more expensive is not automatically better. Full, partial, and failed forecasts must remain visible. Failed-task cost must not disappear from commercial analysis.

### 8. Claims must match the evidence

Synthetic fixtures prove software behavior only. Development and validation performance guide iteration but do not support final claims. Held-out test results support bounded retrospective claims. Prospective results are required before claiming real-world validation.

## Valid research outputs

A forecast record is eligible for experiment accounting only when it has:

- a frozen binary resolution contract;
- a recorded forecast timestamp and evidence cutoff;
- a probability produced before resolution when its status is full or partial;
- a defined profile and reproducible execution identity;
- a final status of full, partial, or failed;
- an outcome resolved from the contract's named source;
- evidence and provenance records required by the selected method.

Full and partial forecasts are eligible only for their protocol-defined probability metrics. Partial forecasts must be reported and analyzed separately. Failed forecasts receive no invented probability and remain in completion, cost, and operational metrics. Neither a partial nor failed run may be silently treated as a successful full forecast.

## Forbidden practices

ForecastLab research must not:

- compare systems on different questions;
- tune on the final test set;
- claim improvement from anecdotal examples;
- accept benchmark improvements without uncertainty estimates;
- add agents without measurable benefit;
- optimize only Brier score while ignoring cost and reliability;
- remove failed or partial runs to improve headline metrics;
- change prompts, models, budgets, evidence rules, or scoring after seeing held-out results;
- treat synthetic results as forecasting-quality evidence;
- claim calibration, superiority, or real-world validation without satisfying the applicable protocol;
- invent benchmark results, academic support, or missing provenance.

## Research governance

Every proposed methodological change requires a written hypothesis and pre-registered success criteria before its validation or test run. The resulting decision must be recorded as accepted, rejected for adoption, or inconclusive. An inconclusive result does not authorize deployment as an improvement.

Exceptions to this charter require a dated decision record that states the reason, scope, owner, and effect on comparability. Exceptions must be visible in any report that uses the affected results.

The forbidden practices in this charter are not waivable exceptions.

## Current limitations

The current MVP remains limited to binary questions, evidence-cutoff backtests, and synthetic software-verification fixtures. Its existing equal-weight logit aggregation is an implemented baseline, not a validated optimum. Model pretraining may contain post-cutoff facts, archive coverage is incomplete, and the present sample sizes do not support a calibration claim. These limitations remain in force until evidence satisfying this charter supersedes them.
