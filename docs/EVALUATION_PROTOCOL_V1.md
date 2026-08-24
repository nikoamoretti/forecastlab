# ForecastLab evaluation protocol V1

Status: required protocol for V1 forecasting-quality claims. No existing synthetic result satisfies this protocol.

This protocol defines how ForecastLab proves whether a method works. It applies to architecture selection, aggregation changes, evidence-policy changes, prompt changes, and any other change presented as a forecasting improvement. Operational bug fixes may be tested separately, but they do not become forecasting-quality evidence by passing software tests.

The governing principles are in [Forecasting Research Charter](FORECASTING_RESEARCH_CHARTER.md). Adoption decisions follow [Experiment Decision Rules](EXPERIMENT_DECISION_RULES.md). The current experiment implementation is described in [Benchmark Experiments](BENCHMARK_EXPERIMENTS.md).

## Claim boundary

An experiment may support only the claim its data and design can answer:

- synthetic fixtures support software-verification claims only;
- development data support debugging and method iteration;
- validation data support configuration selection;
- a frozen held-out test supports a bounded retrospective performance claim;
- prospective unresolved questions support real-world validation after resolution.

No stage alone establishes universal calibration or superiority across domains, horizons, models, or evidence environments.

## Unit of analysis

The primary unit is one binary question with one frozen resolution contract and one outcome. Systems are compared on paired question-level results. Multiple forecasts or reruns of the same question are not independent samples unless the protocol explicitly defines a prospective update analysis.

A forecast is eligible for quality scoring only when:

- the resolution contract was frozen before the forecast;
- the forecast timestamp precedes resolution;
- evidence obeyed the recorded cutoff;
- the system and experiment identities were frozen;
- the resolved outcome follows the named resolution source and rules;
- the probability and run status were stored without post-outcome editing.

Invalid questions and exclusions must be decided without reference to which system performed better. Every exclusion requires a reason and an audit trail.

## Dataset splits

The V1 research corpus consists of four non-overlapping splits.

| Split | Size | Purpose | Permitted use |
| --- | ---: | --- | --- |
| Development | 60 resolved questions | Debugging and iteration | Prompt development, method debugging, instrumentation checks, and exploratory analysis. |
| Validation | 40 resolved questions | Select final configuration | Choose among configurations and freeze the final V1 candidate. No further tuning after selection. |
| Test | At least 100 resolved questions | Final performance claim | One final evaluation of the frozen candidate and required baselines. Never used for tuning. |
| Prospective | 30 to 50 unresolved questions | Real-world validation | Register forecasts before outcomes are known, then score after resolution. |

The bundled synthetic dataset does not count toward any split. V1 quality evaluation cannot begin until real questions with complete provenance and contracts have been assembled and frozen.

### Split construction

Splits must be created before architecture selection and must preserve time order. Whenever possible:

- development questions precede validation questions;
- validation questions precede test questions;
- closely related question families, repeated events, and revisions stay in one split;
- category and horizon distributions are recorded for each split;
- the test period is later than the data used for development and validation;
- prospective questions are unresolved and their forecasts are timestamped before resolution.

Random row assignment is prohibited when it can place near-duplicate questions, later revisions, or the same underlying event in multiple splits. A deterministic split manifest must record question IDs, grouping keys, dates, dataset hashes, and the rule used.

### Dataset freeze and changes

Question text, contract fields, forecast date, resolution date, outcome, provenance, category, and split membership are frozen by content hash. A correction after freeze creates a new dataset version and a written change log. Test outcomes may be added when they legitimately resolve, but questions may not be removed because a system failed on them.

## Required baselines

Every V1 forecasting-method experiment must compare all four configurations on the same eligible questions. Software-only integrity tests are not forecasting-method experiments and cannot support a quality decision or claim.

1. `model_only`
2. `single_agent_equal_budget`
3. `three_track_equal_budget`
4. `hierarchical_forecaster`

Their required roles are:

| Canonical configuration | Purpose | Current MVP mapping or status |
| --- | --- | --- |
| `model_only` | Measures structured forecasting from the frozen contract without retrieval. | Implemented profile: `model_only_v1`. |
| `single_agent_equal_budget` | Measures one research forecaster under the shared research ceiling. | Implemented profile: `single_agent_equal_budget_v1`. |
| `three_track_equal_budget` | Measures independent base-rate, current-evidence, and skeptic tracks under the same total ceiling as the single-agent comparison. | Implemented profile: `three_track_equal_budget_v1`. |
| `hierarchical_forecaster` | Measures the V1 subforecast-graph and scenario method under a pre-registered total ceiling. | Experimental framework profile: `graph_forecaster_v1`; not validated as the required final configuration. |

The current `single_model_forecaster_v1` versus `three_track_forecaster` versus `graph_forecaster_v1` comparison uses ten synthetic questions and is a software-verification experiment, not a V1-complete comparison. The single-model profile executes one direct contract-and-evidence forecasting call without graph construction or aggregation. The graph profile executes the complete Contract, Graph, Evidence Claim, Node Forecast, Graph Aggregation, Forecast Version, and report lifecycle. Adding this diagnostic profile does not replace the four required configurations above.

### Fairness controls

All configurations must receive the same frozen question and resolution contract, forecast cutoff, outcome, model family and version, provider identity, and evidence-eligibility policy unless one of those is the pre-registered treatment. The single-agent, three-track, and hierarchical research configurations must have equal total ceilings for model calls, search calls, fetched documents, tokens, estimated cost, and wall-clock time.

The dedicated ten-question software-verification workflow enforces these controls for its three selected profiles: paired tasks use equivalent question and resolution-contract content, the same per-question forecast cutoff, the same provider and evidence-policy snapshots, and equal total ceilings. Profiles may consume different amounts within those ceilings; cost, latency, and completion remain measured outcomes.

The separate controlled real-dataset runner accepts only frozen `EvaluationDataset` releases. It creates the complete question × profile matrix for `single_model_forecaster_v1`, `three_track_forecaster`, and `graph_forecaster_v1`. One immutable configuration hash binds the canonical question snapshots, cutoffs, profile definitions, model/provider metadata, common ceiling, prompt bundle, pricing catalog, code commit, tracked source, and dependency identity. Execution uses those stored snapshots and refuses dataset or environment drift. Its comparison report contains Brier score, log loss, cost, latency, completion, failure, and evidence-coverage measurements without ranking profiles or asserting superiority. This three-profile diagnostic comparison does not replace the four required V1 configurations above.

The model-only baseline has no search or fetch access by definition. Its lower resource use must be reported, not artificially spent. Equal ceilings do not imply equal actual cost, latency, or completion; those are measured outcomes.

No baseline may be deliberately weakened through inferior prompts, missing contract fields, stale models, or reduced output validation. If one configuration cannot run under the common environment, that is a failure result rather than permission to substitute another provider.

## Metrics

Metrics are reported per configuration and for paired comparisons. Brier score is the primary forecast-quality metric. Other metrics are required diagnostics and decision constraints, not optional decoration.

### Forecast quality

| Metric | Required interpretation |
| --- | --- |
| Brier score | Mean squared error of binary probabilities. Lower is better. Primary paired endpoint. |
| Log loss | Penalizes probability assigned away from the realized outcome. Lower is better. Report with the pre-registered clipping rule. |
| Calibration | Compare predicted probability ranges with observed frequencies by configuration. Report sample sizes and uncertainty; a reliability display is not itself a calibration claim. |
| Sharpness | Measures how concentrated forecasts are away from an uninformative 0.50. Greater sharpness is useful only when calibration and accuracy are maintained. |

Quality metrics must be shown for all valid probabilities and separately for full forecasts. Partial forecasts must remain labeled. Failed forecasts have no invented probability and remain visible through completion and failure metrics.

For `graph_forecaster_v1`, every frozen graph node must produce an eligible node forecast before aggregation. Missing evidence, a failed or invalid node forecast, or aggregation failure makes the run failed. Completed node work and structured failure records remain auditable, but the evaluator must not renormalize a subset or assign a fallback probability.

### Operational

- completion rate;
- partial rate;
- failure rate;
- end-to-end latency;
- total cost.
- evidence coverage, defined as provenance-backed planned research units divided by total planned research units.

Operational denominators include every assigned question. Total cost includes successful requests, failed attempts, searches, partial runs, and failed tasks. Report median and mean cost and latency because tails matter.

Evidence coverage is a pipeline-completeness diagnostic. The graph profile counts nodes whose forecast cites a persisted claim; the legacy profile counts tracks with accepted cutoff-eligible evidence. It does not replace citation precision, unsupported-claim auditing, or forecast-quality scoring.

### Evidence

| Metric | Definition |
| --- | --- |
| Citation precision | Share of audited citations whose stored source and excerpt support the associated factual claim. |
| Unsupported claim rate | Share of audited factual claims that lack adequate supporting evidence. |
| Source quality | Distribution of audited evidence across a pre-registered source-quality rubric. Do not invent a scalar score after seeing results. |
| Cutoff violations | Count and rate of evidence items unavailable or ineligible at the forecast cutoff. Any confirmed violation is a critical validity failure for the affected forecast. |

Evidence metrics require a blinded or system-agnostic annotation process, an annotation guide, and retained adjudication records. The audit sample and sampling rule must be chosen before system labels are revealed to annotators when practical.

### Business

- accuracy improvement per dollar;
- latency tradeoff.

Accuracy improvement per dollar is measured relative to a named baseline using the paired Brier improvement and incremental total cost. It must be accompanied by the raw Brier scores and raw costs. A ratio must not hide a quality regression, near-zero denominator, low completion rate, or uncertainty interval that includes no improvement.

Latency tradeoff reports paired quality difference alongside paired latency difference. A faster or cheaper system is not called more accurate, and a more accurate system is not called operationally preferable without the stated tradeoff.

## Analysis populations

Every report must include:

- **assigned population:** every question assigned to the configuration;
- **all-valid population:** full and partial runs with a valid probability;
- **full-only population:** successful non-partial runs;
- **paired population:** questions with eligible comparable results for every configuration in the stated comparison.

The primary paired Brier analysis uses the pre-registered paired population and reports its coverage relative to the assigned population. Completion, partial, failure, and cost results always use the assigned population. A high paired score with low coverage cannot support adoption.

## Statistical requirements

### Paired question-level comparison

For each question, compute the candidate's metric minus the baseline's metric. For Brier and log loss, a negative mean difference favors the candidate. Report the mean paired difference, question count, win/tie/loss counts when applicable, and the underlying configuration scores.

### Bootstrap confidence intervals

Report a pre-registered 95% bootstrap confidence interval over paired question-level differences. The bootstrap seed, sample count, interval method, and minimum sample rule must be frozen before the validation or test run. Cluster or group resampling must replace simple row resampling when multiple questions share the same underlying event family.

A small improvement without statistical confidence is not sufficient evidence. If the interval includes zero, the change is not accepted as a demonstrated quality improvement.

### Temporal splits

Development, validation, and test data must respect temporal ordering and question-family grouping. The evidence cutoff for each historical forecast is its forecast date. Model-pretraining leakage remains a limitation and must be disclosed.

### No random leakage

The same event, near-duplicate question, resolution record, or outcome-revealing source must not cross splits in a way that exposes the answer. Prompt examples, retrieval fixtures, manually written rationales, and calibration fitting are also data and must obey split boundaries.

### Configuration selection

Development results may generate candidates. Validation selects exactly one final V1 candidate and freezes it. The test set is evaluated once for the final claim. A failed test does not authorize returning to validation, selecting a different candidate, and reusing the same test as if it were untouched.

When many candidates or metrics are explored, the experiment record must identify the pre-registered primary comparison and primary endpoint. Secondary findings are labeled exploratory.

## Prospective validation

Prospective questions are registered while unresolved. Each record must include the contract, forecast timestamp, cutoff, system identity, probability, update policy, and expected resolution source. Outcomes are resolved without reference to which system predicted them better.

Updates are analyzed as timestamped forecast versions under a pre-registered rule. The final pre-resolution update must not replace the initial forecast silently. The prospective report must show both question-level and version-level coverage, costs, failures, and deviations from the registered protocol.

Thirty to fifty prospective questions are an initial validation set, not definitive proof of calibration across all probability ranges or domains.

## Experiment workflow

1. Write the hypothesis and decision criteria.
2. Freeze dataset split, configurations, identities, budgets, evidence policy, metrics, and analysis code.
3. Run software-integrity checks and confirm no paid or real experiment is accidentally using mock evidence.
4. Execute every configuration on the paired question set.
5. Audit failures, evidence, cutoff compliance, and exclusions before interpreting observed differences.
6. Compute the frozen analyses and uncertainty intervals.
7. Apply [Experiment Decision Rules](EXPERIMENT_DECISION_RULES.md).
8. Publish an immutable internal decision record, including negative and inconclusive results.

ForecastLab's controlled runner implements the frozen assignment and measurement substrate for steps 2 through 4. The read-only analysis endpoint now implements deterministic paired percentile-bootstrap intervals for the three controlled profile pairs, using 2,000 samples, seed `20260823`, a 95% interval, and a 20-pair minimum reporting gate. It does not yet implement pre-registration enforcement, automatic split manifests, clustered resampling, multiplicity adjustment, or a decision rule.

The research-analysis layer reads those immutable measurements and reports per-profile Brier score, log loss, five fixed calibration buckets, operations, evidence diagnostics, cost ratios, and paired uncertainty intervals. Human reviewers may add internal failure classifications without altering forecast records. The intervals quantify sampling uncertainty for the observed paired questions; they do not establish representativeness, causal attribution, calibration, or an adoption decision. See [Forecast Research Analysis](FORECAST_RESEARCH_ANALYSIS.md).

## Required report contents

An evaluation report must include dataset and split hashes, sample sizes, question coverage, system identities, profile and prompt hashes, provider and model identity, budgets, pricing snapshot, evidence policy, code and dependency identity, primary and secondary metrics, confidence intervals, exclusions, full/partial/failed counts, total cost, limitations, and the final decision.

Results must be labeled development, validation, test, or prospective. Synthetic results must retain the existing software-verification warning.

## Current limitations

ForecastLab has not yet assembled the required real split corpus or validated the experimental graph configuration as the required hierarchical method. The current aggregator is a simple importance-weighted log-odds rule; it displays dependencies but does not model them probabilistically or synthesize scenarios. Existing calibration displays use a minimum sample gate, but that gate is not evidence of calibration. The paired bootstrap treats questions as independent and needs a pre-registered clustered variant when questions share an event family. Its six unadjusted intervals require joint interpretation. Evidence-cutoff backtests cannot eliminate knowledge embedded in model weights. Until the full protocol is completed, methodology claims remain hypotheses.
