# ForecastLab V1 method specification

Status: intended V1 forecasting architecture. This is a conceptual specification, not an implementation claim.

This document defines the method ForecastLab V1 must implement and evaluate. The current MVP contains resolution contracts, Forecast Contracts, Forecast Graphs, node-linked Evidence Claims, a complete opt-in graph execution path, independent legacy research tracks, evidence provenance, node forecasts, and deterministic graph aggregation. Scenario synthesis, dependency-aware probability modeling, and forecasting-quality validation remain V1 requirements that are not yet implemented or proven.

The governing principles are in [Forecasting Research Charter](FORECASTING_RESEARCH_CHARTER.md). Evaluation and adoption are governed by [Evaluation Protocol V1](EVALUATION_PROTOCOL_V1.md) and [Experiment Decision Rules](EXPERIMENT_DECISION_RULES.md).

## Forecast lifecycle

```text
Question
↓
Forecast Contract generation
↓
Human review and approval
↓
Reference-class guidance
↓
Forecast Graph generation, validation, and freeze
↓
Evidence collection
↓
Document retrieval and cutoff validation
↓
Evidence Claim extraction and node linkage
↓
Node-level forecasts or independent legacy research tracks
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

Evidence is collected against the graph and track charters. Search results are discovery aids; only retrieved, stored, eligible documents may produce Evidence Claims. Each claim is linked to one Forecast Node before it can become forecasting context. Backtests follow [Evidence Cutoff Policy](EVIDENCE_CUTOFF_POLICY.md).

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
| Importance | A pre-aggregation estimate of how materially the node can affect the parent forecast. In `graph_forecaster_v1`, it is the raw aggregation weight before normalization. It is not confidence. |
| Dependencies | Other nodes or common causes that make the node conditionally related. |
| Preferred evidence | Source types, records, and time windows best suited to answer the node. |
| Output type | Probability, directional update, bounded quantity, scenario weight, or structured categorical result. |

The graph must be acyclic for calculation, retain stable node identifiers, and record why a dependency exists. If two nodes reuse the same evidence or derive from the same upstream fact, that relationship must be represented rather than treated as independence.

The graph is frozen before final evidence synthesis for an evaluation run. Development experiments may revise graph-generation rules, but validation and test runs may not change them after outcomes or scores are observed.

### Forecast Graph lifecycle

1. **Eligibility:** only an `approved` Forecast Contract may be used to generate a graph. The approved contract's normalized binary outcome is the graph root and is not rewritten by the generator.
2. **Generate:** `GraphGenerator` returns structured JSON containing 5–10 question-specific research nodes. Every generated graph includes at least one `base_rate`, `driver`, `adversarial`, and `resolver` node. Other permitted node types are `trend`, `dependency`, and `scenario`.
3. **Relate:** each node records an optional parent, explicit node dependencies, preferred source guidance, required output type, and an importance weight. Importance expresses materiality to the outcome, not confidence. The graph forecaster uses it as the raw aggregation weight under the fixed calculation below.
4. **Validate:** approval requires a root outcome, at least three nodes, an adversarial node, a resolver node, unique node identifiers and questions, valid parent and dependency references, and no cycles. Generator output also has to satisfy the stricter 5–10-node and required-type constraints.
5. **Approve and freeze:** the current minimal API has no graph editor or separate human approval endpoint. `POST /api/contracts/{id}/graph` validates generated output and stores it directly as `approved`; invalid output is rejected rather than partially stored. Repeating the request returns the existing approved graph.
6. **Gate:** a question created through the first-class Forecast Contract flow cannot start forecasting until its current approved contract has an approved graph. Legacy questions without first-class contracts remain a compatibility boundary.
7. **Supersede:** the data model reserves `superseded` for a future versioning workflow. This foundation does not expose graph regeneration or mutation after approval.

The opt-in `graph_forecaster_v1` profile executes the approved graph. The legacy profiles still use independent tracks and their existing aggregation. The graph profile retains declared relationships for planning and audit, and uses importance weights through the deterministic rule below.

## Evidence model

Every factual claim must map to:

- source;
- excerpt;
- publication date, when known;
- retrieval date;
- source-availability timestamp;
- temporal basis;
- cutoff eligibility;
- source classification;
- confidence.

For this specification:

- **source** means the stored canonical URL or immutable record identifier;
- **excerpt** means the smallest stored passage or record fields that support the claim;
- **publication date** means the date attributed to publication by reliable source metadata; it may be unknown and is never synthesized from retrieval time;
- **retrieval date** means when ForecastLab obtained the stored content;
- **source-availability timestamp** means the time ForecastLab can prove the source bytes or immutable record were available for the run;
- **temporal basis** records whether eligibility rests on a publication date, verified snapshot date, or live retrieval date;
- **cutoff eligibility** records whether the source-availability proof satisfies the applicable live-run boundary or historical cutoff and why;
- **source classification** distinguishes primary, secondary, and other pre-registered source classes;
- **confidence** is the system's confidence that the evidence supports the mapped claim, not confidence that the forecast will resolve yes.

Evidence records must also retain a content hash, title or record description, publisher when available, track and node usage, and rejection reason when ineligible. Duplicate URLs or materially duplicated content must be detected so repetition is not mistaken for independent corroboration.

An inference may combine multiple evidence items, but it must link to them and be labeled as an inference. An unsupported claim must be removed from the forecast packet or recorded as a failure.

### Evidence Claims lifecycle

1. **Retrieve:** a fetched document retains its canonical URL, title, publisher, optional publication date and its discovery source, required retrieval and source-availability timestamps, typed temporal basis, content hash, cutoff status, and any rejection reason. Search-provider dates remain hints; a search hit alone cannot produce a claim.
2. **Assign:** extraction is requested for one explicit Forecast Node and one stored `EvidenceItem`. The node identifier is mandatory; there is no unassigned Evidence Claim.
3. **Extract:** `EvidenceExtractor` requests structured JSON containing the factual claim, an exact document excerpt, support or refutation stance, claim-support confidence, source quality, and primary-source assessment. Source metadata is copied from the fetched document rather than accepted from model output.
4. **Validate:** extraction fails closed when the document is rejected or lacks URL, title, publisher, text, or source-availability provenance. Live mode may accept an undated page when its content was retrieved and hashed during the run; the publication date remains null, the temporal basis is `retrieval_date`, and the limitation is reported. Backtest mode rejects retrieval-basis evidence and requires a verified final archive snapshot or another explicitly supported immutable historical timestamp at or before `as_of`. A known publication date after the cutoff still rejects the document. Each excerpt must occur in the retrieved document. A malformed batch is rejected in full rather than partially accepted.
5. **Persist:** `EvidenceClaim` stores the parent evidence-item and node identifiers together with the copied provenance and assessment fields. Persistence verifies that both parents exist, the parent evidence item is eligible, its source metadata matches the claim, and the evidence item and node belong to the same forecasting question.
6. **Review:** `GET /api/nodes/{id}/evidence` presents Node → Claims → Sources, while `GET /api/evidence/{id}` returns a full individual claim. The minimal UI exposes this chain without adding an evidence editor.
7. **Context gate:** only claims with verified mode-appropriate availability and all mandatory provenance fields are eligible for a forecasting context. Live retrieval-basis claims remain eligible; historical retrieval-basis claims are rejected even when their current-page retrieval occurred before execution completed. Rejected evidence and invalid claims are excluded by the claim-context selector.

The opt-in graph execution path invokes this layer for each researched node. Legacy track prompts continue to receive their existing evidence packet and are unchanged. Semantic entailment beyond exact-excerpt grounding remains an extractor-model assessment that future evaluation must measure.

### Graph forecaster V1 execution lifecycle

1. **Opt in:** `graph_forecaster_v1` selects the graph-node execution strategy. All existing profiles default to the unchanged legacy-track strategy.
2. **Require contract:** execution fails before research unless the question has an `approved` first-class Forecast Contract. A legacy `ResolutionContractRow` alone does not satisfy this gate.
3. **Ensure graph:** the worker loads the latest approved graph for that approved contract. If none exists, it generates, validates, stores, and commits the graph before starting node research.
4. **Freeze a Research Plan:** Research Planner V2 (`graph_research_planner_v2`) computes `importance_weight + dependency_count + uncertainty_score`, retains every critical node, removes duplicate noncritical research questions, and selects the highest-value workload. Defaults cap the plan at eight researched nodes, five searches per node, and twenty Evidence Claims total. For each node, the maximum logical-call envelope is `research_plan_calls (1) + primary_extraction_calls (allocated fetches) + extraction_retry_calls (allocated fetches when the model-backed smaller-chunk fallback is enabled, otherwise 0) + node_forecast_calls (1)`. The deterministic document fallback is not a model call. The planner includes already-spent graph-generation and run resources once, scales extraction estimates with allocated documents, and checks call, token, search, fetch, estimated-cost, and wall-clock dimensions. It reduces noncritical breadth to preserve end-to-end completion and fails before search or research if the critical or three-node minimum cannot fit. The selected and skipped node IDs, scores, reasons, planner version, per-phase calls/tokens/cost, resource tier, per-node allocations, forecast-call reserve, headroom, and deterministic persistence order are stored as one immutable `ResearchPlan` per Forecast Run.
5. **Research selected nodes:** before concurrent workers begin, the shared locked budget freezes the plan's call roles and protects one node-forecast call for every selected node. A research worker cannot borrow that reserve, parallel workers cannot oversubscribe it, and a smaller-chunk extraction retry can run only from that node's persisted retry allocation. When no retry was planned, execution proceeds directly from a failed primary extraction to the existing low-confidence verbatim document fallback. Unused retry capacity remains an unused planning value; it does not create provider or ledger usage. `GraphResearchExecutor` otherwise keeps the same queries, ranking, temporal validation, extraction semantics, and fallback claim. Evidence persistence and node forecasting follow deterministic dependency order. Unselected nodes retain audited skip reasons and receive no imputed forecast.
6. **Build forecasting context:** `NodeForecaster` receives exactly the approved Forecast Contract, the node question, and eligible Evidence Claims linked to that node. Raw documents and webpages are excluded. At least one eligible claim is required before the model call.
7. **Generate and validate node forecast:** the model returns a structured probability in `[0, 1]`, reasoning, selected supporting and opposing claim identifiers, and uncertainty notes. Missing or out-of-range probabilities, malformed output, absent claim references, unknown claim identifiers, cross-node references, and support/refutation stance mismatches are rejected. The complete executor additionally requires the open interval `(0, 1)` because exact zero or one has infinite log odds. Code derives confidence from the selected claims as `min(1, total cited provenance strength / 2)`, where claim strength is `claim confidence × source quality × source factor` and the source factor is `1.0` for primary sources and `0.85` otherwise. Confidence is an audit field and does not alter graph aggregation.
8. **Persist node output:** each successful `ForecastNodeRun` stores the node probability, derived confidence, model reasoning, supporting and opposing claim identifiers, uncertainty notes, model identity, raw importance weight, normalized weight, run identity, node identity, and creation time. Signed log-odds contributions are stored on the first-class `ForecastAggregation`, not in the legacy non-negative probability-contribution column.
9. **Apply node-failure policy:** evidence failures are classified as `retrieval_failure`, `no_matching_source`, `cutoff_rejection`, or `extraction_failure` and retain the node ID, plan, queries, checked sources, and reason. Nodes with importance weight at least `0.8` are critical and are always selected. Any critical failure, or fewer than three successful selected nodes, stops without a probability. A noncritical selected-node failure is excluded, never imputed, and marks the result as reduced research confidence with an importance-weight coverage factor. Planned skips are reported separately and are not execution failures.
10. **Aggregate graph:** `GraphAggregator` receives exactly one valid node forecast for every node in the eligible researched subset. Its math remains `importance_weighted_log_odds_v1`: normalize included graph importance weights, convert probabilities to log odds, sum weighted log-odds contributions, and apply the logistic function. It still rejects missing or duplicate forecasts within that subset, mixed run identities, outside nodes, invalid probabilities or weights, and zero total weight. The calculation trace explicitly lists tolerated exclusions before the unchanged calculation.
11. **Persist final artifacts:** a successful transaction writes the Research Plan, eligible node runs, any tolerated structured failures, one immutable `ForecastAggregation`, and one `ForecastVersion` with the exact aggregation probability. A critical graph, planning, evidence, node, or aggregation failure writes durable failure records and any completed node runs, marks the run failed, and creates neither an aggregation nor a Forecast Version.
12. **Report:** the graph report exposes the approved contract, full graph, selected and skipped research nodes, per-node allocations, Evidence Claims with sources, excerpts, optional publication dates, publication verification, source-availability timestamps, retrieval dates, temporal bases, and cutoff verification, node probabilities and reasoning, structured failed-node research audits, normalized weights, signed log-odds contributions, calculation trace, research coverage impact, and final answer. It never displays retrieval time as publication time. Fatal runs expose their stage and failures with a null final probability; tolerated failures remain visible beside a reduced-confidence result.

The initial aggregation is deliberately simple. Declared graph dependencies remain visible in the audit record but do not yet alter the mathematical combination. It is not a Bayesian network, dependency model, or calibration claim. Reproducibility depends on the frozen graph, model and prompt identity, node probabilities, selected claim IDs, claim assessments, uncertainty notes, and profile version. The aggregator sorts by stable node ID and uses fixed arithmetic, so input ordering cannot change the result.

`POST /api/forecasts/{id}/execute-graph` starts the complete path and `GET /api/forecasts/{id}/graph-report` returns its joined audit report. The existing `POST /api/forecasts/{id}/execute-v1`, `POST /api/forecasts/{id}/node-runs`, and `GET /api/forecasts/{id}/node-runs` endpoints remain available. `POST /api/questions/{id}/runs` also dispatches the complete path when `graph_forecaster_v1` is selected. No legacy profile or endpoint is removed.

### Graph live smoke profile and cost preflight

`graph_forecaster_v1` is the versioned production/evaluation architecture profile. `graph_live_smoke_v1` is a separate operational smoke profile that enters the same `GraphForecastExecutor`, uses the same Research Planner, aggregation method, and prompt versions, and enables graph generation, Evidence Claims, node forecasting, and graph aggregation. Its version 1 limits are 14 model calls, 4 searches, 8 fetched documents, 50,000 tokens, 1,536 output tokens per call, $0.50 estimated lifetime cost, and 300 seconds. The four-search ceiling limits a normal plan to at most four search-backed nodes; the Planner V2 call envelope may reduce it further. With one graph-generation call already consumed, one fetched document and enabled smaller-chunk retry per node, 13 calls remain and the four-call envelope permits three complete nodes with one call of headroom. This is derived from live resource dimensions, not a hard-coded three-node product rule. The profile is intentionally excluded from default scientific experiment profile sets.

For live and non-synthetic backtest launch preflight, code calculates three values from the effective provider identities and committed conservative pricing catalog:

1. the model upper bound using the profile token limit and the existing token split;
2. the search upper bound using planned search calls times `estimate_search_cost()` for the effective search provider;
3. the total upper bound as their sum.

The total is compared with the effective Settings/profile ceiling before a Forecast Run is created. If either required catalog estimate is unavailable, the missing component and total remain explicitly unavailable and execution fails closed. These values are stored in the JSON `ExecutionContext` and exposed by execution preview and rejected-launch preflight responses. They are conservative planning estimates, not provider invoices or a vendor-enforced cap. A successful `graph_live_smoke_v1` run establishes only that the bounded operational path completed; it does not establish forecasting accuracy, calibration, reliability at production scale, or superiority over another profile.

### Single-model evaluation baseline

`single_model_forecaster_v1` is an isolated direct baseline. It requires the same approved Forecast Contract as graph execution, derives one deterministic research query, and builds one evidence packet through the existing search, fetch, cutoff, cache, budget, and provider-ledger seams. One structured model call receives only the contract and that packet and returns one probability, reasoning, uncertainty, and selected evidence IDs. Code validates the response and persists the direct result as a normal Forecast Version. The profile does not generate Forecast Graph nodes, extract Evidence Claims, create node forecasts, or invoke probability aggregation. Its `direct_model_probability_v1` calculation record is persistence and report metadata, not a mathematical combination.

### Integrated weighted-log-odds aggregation

`GraphAggregator` is the deterministic aggregation policy for `graph_forecaster_v1`. It accepts one frozen Forecast Graph and a complete set of `ForecastNodeRun` records from exactly one forecast run. It rejects missing or duplicate node forecasts, forecasts for nodes outside the graph, mixed run identifiers, missing or invalid importance weights, zero total weight, and node probabilities outside the open interval `(0, 1)`. The open interval is required because exact zero and one have infinite log odds.

For each node `i`, code calculates:

1. `normalized_weight_i = importance_weight_i / sum(importance_weights)`;
2. `log_odds_i = ln(probability_i / (1 - probability_i))`;
3. `contribution_i = normalized_weight_i × log_odds_i`;
4. `combined_log_odds = sum(contribution_i)`;
5. `final_probability = 1 / (1 + exp(-combined_log_odds))`.

Nodes are processed by stable node identifier, so input ordering cannot change the contribution list or calculation trace. `ForecastAggregation` stores the method, final probability, full node contributions, calculation trace, run identity, and creation time. The database permits one immutable first-class aggregation per forecast run; an identical repeat is idempotent and a conflicting replacement is rejected.

`GraphForecastExecutor` calls this service only after the critical-node gate has produced a complete eligible graph subset. It never asks the aggregator to impute a failed node. It persists the first-class aggregation before creating the Forecast Version. The older dependency-discounted graph function remains available as a compatibility implementation but is not selected by version 5 of `graph_forecaster_v1`.

### V1 report and first experiment lifecycle

1. **Assemble the report:** a completed graph run joins its approved Forecast Contract, frozen Forecast Graph, `ForecastNodeRun` rows, persisted Evidence Claims, and first-class Forecast Aggregation. Node sections show the node question, probability, confidence, reasoning, raw and normalized weights, log odds, signed contribution, and supporting and opposing cited claims with excerpts and source links. Unselected node-linked claims remain visible but are not presented as calculation inputs.
2. **Expose one calculation:** JSON, Markdown, and the report screen use the same structured V1 report payload. The calculation trace shows weight normalization, every node log-odds contribution, their sum, and the logistic conversion. It is an audit record, not a natural-language reconstruction.
3. **Freeze the first comparison:** the dedicated workflow fixes ten synthetic binary questions and exactly three profiles, `single_model_forecaster_v1`, `three_track_forecaster`, and `graph_forecaster_v1`, creating thirty ordinary asynchronous benchmark tasks. The single-model profile is the one-call direct baseline; the legacy identifier is an experiment alias for the existing three-track execution path. Each paired task receives equivalent question and resolution-contract content, the same forecast cutoff and provider identity, and equal ceilings for model calls, searches, fetched documents, tokens, estimated cost, and wall-clock time.
4. **Measure without selecting a winner:** profile and paired outputs include Brier score, log loss, total cost, latency, full completion rate, and evidence coverage. Graph evidence coverage is the fraction of planned nodes whose node forecast cites at least one persisted Evidence Claim. Legacy coverage is the fraction of research tracks with accepted cutoff-eligible evidence.
5. **Retain the claim boundary:** this ten-question dataset is synthetic. It verifies execution, persistence, metric calculation, export, and UI behavior. It does not satisfy the real-data splits, baseline set, sample size, annotation audit, or prospective requirements in `EVALUATION_PROTOCOL_V1.md`, and no superiority claim may be drawn from it.

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
