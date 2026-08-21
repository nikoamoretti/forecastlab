# ForecastLab V1 method specification

Status: intended V1 forecasting architecture. This is a conceptual specification, not an implementation claim.

This document defines the method ForecastLab V1 must implement and evaluate. The current MVP already contains resolution contracts, independent research tracks, evidence provenance, and deterministic aggregation. Dynamic subforecast graphs, scenario synthesis, and the `hierarchical_forecaster` comparison are V1 requirements that are not yet proven or necessarily implemented.

The governing principles are in [Forecasting Research Charter](FORECASTING_RESEARCH_CHARTER.md). Evaluation and adoption are governed by [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md) and [Experiment Decision Rules](EXPERIMENT_DECISION_RULES.md).

## Forecast lifecycle

```text
Question
↓
Forecast Contract generation
↓
Human review and approval
↓
Reference class
↓
Forecast decomposition
↓
Evidence collection
↓
Independent research tracks
↓
Scenario synthesis
↓
Probability calculation
↓
Report generation
↓
Monitoring and updating
```

Each stage must leave a stored, auditable artifact. A later stage may reject an earlier artifact as invalid, but it must not silently rewrite it.

### 1. Question

V1 accepts binary questions about future events. The original wording, author-supplied context, creation time, and intended forecast cutoff must be retained. A question is not forecastable until its resolution contract passes review.

### 2. Forecast Contract

The Forecast Contract converts ordinary wording into explicit scoring rules and research guidance. It is frozen for an experiment before any forecast is generated. A material contract change creates a new contract version and cannot be applied retroactively to improve a score.

### 3. Reference class

The system identifies one or more historical or structural reference classes, states why each is relevant, and records important differences from the present case. If no defensible reference class is available, that absence must be explicit. A fabricated or weak analogue must not be presented as a base rate.

### 4. Forecast decomposition

The system creates a question-specific graph of subforecasts and drivers. The graph describes what must be estimated, which nodes depend on others, and how each node can affect the final event.

### 5. Evidence collection

Evidence is collected against the graph and track charters. Search results are discovery aids; only retrieved, stored, eligible material may support factual drivers. Backtests follow [Evidence Cutoff Policy](EVIDENCE_CUTOFF_POLICY.md).

### 6. Independent research tracks

Three tracks independently analyze the frozen contract and their assigned research objective. Independence ends only after each track has submitted its probability and structured support.

### 7. Scenario synthesis

The system constructs a small set of decision-relevant scenarios that explains how material nodes could combine. Scenarios expose interactions and tail risks; they do not authorize untraceable probability adjustments.

### 8. Probability calculation

Structured forecasts, priors, scenarios, dependencies, and method settings are passed to a deterministic calculation owned by code. The calculation and all parameter values are frozen as part of the profile.

### 9. Report generation

The report presents the final probability first, followed by contract, method identity, evidence, drivers, disagreement, uncertainty, failure status, cost, and limitations. Report prose cannot change the calculated probability.

### 10. Monitoring and updating

Monitoring identifies potentially material changes. It does not silently rerun or overwrite a forecast. An update is a new forecast version with a new cutoff, fresh evidence eligibility checks, a stated trigger, and a link to the preceding version.

## Question compiler

The question compiler must produce an editable resolution contract with these required fields:

| Field | Requirement |
| --- | --- |
| Exact yes outcome | States the observable condition scored as 1. |
| Exact no outcome | States the observable condition scored as 0, including boundary cases. |
| Resolution date | Names the deadline and time zone used for scoring. |
| Authoritative source | Names the primary resolver and the exact record or series when known. |
| Fallback source | Defines an ordered fallback when the authoritative source is unavailable. An empty fallback must be explicit. |
| Ambiguity rules | Resolves wording, revisions, preliminary releases, rounding, and boundary conditions. |
| Cancellation rules | Defines treatment of cancellation, discontinuation, postponement, or an unresolvable event. |
| Resolver risk | Records ways official scoring could differ from an ordinary-language interpretation. |

The compiler must reject a contract when yes and no are not mutually exclusive, both could be false without a cancellation rule, the resolver cannot be identified, or the resolution date is not operationally usable.

### Forecast Contract lifecycle

1. **Generate:** the compiler normalizes the original question and returns structured JSON containing the outcome, resolution, ambiguity, metadata, and initial research-guidance fields. Generation does not begin research and does not estimate a probability.
2. **Draft:** the generated contract is stored as `draft` with its own identifier, question identifier, version, creation time, and creator. Drafts may be incomplete, but their missing fields must remain explicit.
3. **Review:** a human reviews the normalized question, yes and no conditions, resolution date, authoritative and fallback sources, resolution method, ambiguity notes, cancellation conditions, and resolver risks.
4. **Approve:** a draft may become `approved` only when `yes_condition`, `no_condition`, `resolution_date`, `authoritative_source`, and `resolution_method` are present and the outcome conditions differ. Approval is the gate before the new contract flow starts research.
5. **Freeze:** an approved contract supplies the immutable resolution inputs to forecasting. The initial reference class, suggested drivers, and known dependencies are research guidance only; they are not evidence and do not determine the forecast probability.
6. **Supersede:** a material correction creates a higher contract version. Approving it marks the prior approved version `superseded`; historical forecasts retain the version they used.

The first implementation stores Forecast Contracts separately from legacy resolution-contract rows. Approval writes the approved outcome and resolution fields into the legacy shape so existing forecast records and execution remain compatible. Legacy entry points that predate the Forecast Contract API remain a compatibility boundary and must not be described as V1-conforming unless they enforce the same approval gate.

## Forecast decomposition

### Dynamic subforecast graph

Decomposition is question-specific rather than a fixed list of prompts. A forecast should create relevant nodes such as:

- historical base rate;
- current trend;
- institutional behavior;
- leading indicators;
- external shocks;
- opposing scenarios;
- resolver behavior.

Not every question needs every node. Each included node must earn its place by representing a distinct uncertainty or decision-relevant driver.

Each node must define:

| Field | Meaning |
| --- | --- |
| Question | A resolvable or estimable subquestion stated without assuming the parent answer. |
| Importance | A pre-aggregation estimate of how materially the node can affect the parent forecast. It is not confidence. |
| Dependencies | Other nodes or common causes that make the node conditionally related. |
| Preferred evidence | Source types, records, and time windows best suited to answer the node. |
| Output type | Probability, directional update, bounded quantity, scenario weight, or structured categorical result. |

The graph must be acyclic for calculation, retain stable node identifiers, and record why a dependency exists. If two nodes reuse the same evidence or derive from the same upstream fact, that relationship must be represented rather than treated as independence.

The graph is frozen before final evidence synthesis for an evaluation run. Development experiments may revise graph-generation rules, but validation and test runs may not change them after outcomes or scores are observed.

## Evidence model

Every factual claim must map to:

- source;
- excerpt;
- publication date;
- retrieval date;
- cutoff eligibility;
- source classification;
- confidence.

For this specification:

- **source** means the stored canonical URL or immutable record identifier;
- **excerpt** means the smallest stored passage or record fields that support the claim;
- **publication date** means when the source became available, or an explicit unknown value;
- **retrieval date** means when ForecastLab obtained the stored content;
- **cutoff eligibility** records whether the content was available by the forecast cutoff and why;
- **source classification** distinguishes primary, secondary, and other pre-registered source classes;
- **confidence** is the system's confidence that the evidence supports the mapped claim, not confidence that the forecast will resolve yes.

Evidence records must also retain a content hash, title or record description, publisher when available, track and node usage, and rejection reason when ineligible. Duplicate URLs or materially duplicated content must be detected so repetition is not mistaken for independent corroboration.

An inference may combine multiple evidence items, but it must link to them and be labeled as an inference. An unsupported claim must be removed from the forecast packet or recorded as a failure.

## Independent tracks

The conceptual role names map to the current persisted track identifiers as follows: base-rate researcher to `base_rate`, evidence researcher to `current_evidence`, and skeptic researcher to `skeptic`.

### 1. Base-rate researcher

Purpose: find historical analogues.

The base-rate researcher identifies reference classes, estimates historical frequency where defensible, and explains relevant similarities and differences. It must state when sample quality or comparability is weak.

### 2. Evidence researcher

Purpose: analyze current information.

The evidence researcher evaluates current state, trends, official records, leading indicators, and institution-specific information available by the cutoff. It must distinguish observed facts from forecasts made by sources.

### 3. Skeptic researcher

Purpose: find reasons the consensus is wrong.

The skeptic researcher searches for disconfirming evidence, hidden dependencies, reversal mechanisms, tail events, wording risk, and resolver behavior. It must challenge both yes and no narratives rather than defaulting to pessimism.

Tracks receive the same frozen resolution contract and forecast cutoff. They may receive distinct evidence assignments. Tracks must not see each other's conclusions, probabilities, reasoning summaries, or scenario weights before aggregation. Shared model state, shared retrieved documents, or shared graph nodes must be disclosed as potential dependence.

Each track returns a probability, prior when applicable, cited drivers, counterarguments, unresolved uncertainties, evidence-quality assessment, and resolver-risk assessment. A failed track remains failed; another track must not impersonate it.

## Scenario synthesis

Scenario synthesis turns material graph paths into a small, named set of plausible ways the event could resolve. Each scenario must specify:

- the conditions that define it;
- the nodes on which it depends;
- supporting and contradicting evidence;
- a weight or bounded weight range;
- its implication for the parent event;
- overlap with other scenarios.

Scenario weights must be validated by code. Mutually exclusive and collectively exhaustive scenarios must sum to 1. Scenarios that overlap must not be represented or summed as if mutually exclusive; their interaction must follow the profile's frozen dependency rule. A residual or other scenario is required when the named mutually exclusive scenarios are not collectively exhaustive.

## Probability model

**The LLM proposes reasoning. Code calculates probabilities.**

The V1 probability model must handle the following elements without hiding them in report prose.

### Priors

A prior comes from a defensible reference class or an explicitly neutral fallback. The source and sample behind a base rate must be recorded. When no reliable base rate exists, the method must label the prior as weak rather than manufacture precision.

### Evidence updates

Tracks propose the direction, importance, and rationale of evidence updates in a structured form. Code validates allowed ranges and applies the profile's frozen update rule. The same underlying fact must not be counted repeatedly through multiple citations or nodes.

### Scenario weights

Scenario weights are structured inputs subject to code validation. The profile defines whether they are point values or bounded values and how they influence the parent probability. The model must not alter weights after seeing the computed result merely to make the number feel plausible.

### Dependency handling

Dependencies are explicit graph edges or common-cause groups. The V1 method may group, cap, or condition dependent contributions, but the chosen rule must be simple, deterministic, documented, and compared with a simpler baseline. Assuming independence by omission is not permitted.

### Aggregation

Aggregation is a versioned code-owned function. It must expose included and missing inputs, clipping or bounds, weights, dependency adjustments, and the final calculation. The current equal-weight logit mean with fixed shrinkage remains an implemented baseline. It is not presumed to be the optimal V1 method.

The required `hierarchical_forecaster` configuration uses the frozen subforecast graph and a deterministic rule to combine child-node and scenario outputs into the parent probability. Its exact combination rule is a research decision that must be pre-registered and justified before validation. Advanced mathematics must not be introduced unless a simpler method has failed and the new method can be evaluated fairly.

### Calibration

Calibration is evaluated after outcomes are known; it is not inferred from persuasive reasoning. Any calibration transform must be fitted on development data only, selected on validation data, and frozen before the test set is scored. If the available data are insufficient, V1 must use no learned calibration transform and must label outputs as uncalibrated estimates.

## Report contract

A V1 report must include:

- final probability and forecast timestamp;
- resolution contract and cutoff;
- profile, model, prompt, code, and evidence-policy identity;
- prior and reference-class limitations;
- included, missing, and failed graph nodes and tracks;
- scenario and dependency disclosures;
- factual drivers linked to evidence;
- counterarguments and unresolved uncertainties;
- aggregation trace;
- full, partial, or failed status;
- cost, latency, and provider-attempt accounting;
- an explicit limitations statement.

Longer reports are not preferred. The report should contain the minimum material needed to understand and audit the number.

## Reproducibility contract

Every evaluation forecast must freeze the dataset, resolution contract, graph-generation rule, graph, evidence cutoff, evidence policy, prompts, models, profiles, aggregation and calibration rules, pricing snapshot, resource ceilings, code identity, and dependency identity. API keys and authorization material must never be stored in the freeze.

A rerun under a changed identity is a new experiment, not a reproduction. A monitoring update is a new forecast version, not an edit to the original forecast.

## Open V1 method decisions

These decisions require pre-registered development and validation work before implementation can be called V1-conforming:

- the graph-generation constraints and maximum graph complexity;
- the deterministic hierarchical combination and dependency rules;
- the scenario representation for overlapping rather than mutually exclusive cases;
- the evidence-support confidence rubric and source-quality rubric;
- the minimum evidence coverage required for a full forecast;
- the handling of missing high-importance nodes without hiding partial status;
- whether any calibration transform has enough development data to be justified;
- the monitoring cadence and rule for selecting forecast versions in prospective evaluation.

## V1 non-claims

This specification does not establish that dynamic decomposition, three-track research, scenario synthesis, or hierarchical aggregation improves forecasts. It defines how those methods must behave so they can be tested. Until the protocol is completed, ForecastLab must not claim calibration, superiority, or real-world validation.
