# Relationship-Aware Aggregation V1

`relationship_mass_conserving_log_odds_v1` is the deterministic aggregation policy selected by `graph_forecaster_v1` version 8. It reduces repeated influence among directly related Forecast Graph nodes while conserving the graph's complete canonical importance mass. It does not change any node-authored probability.

This policy is an auditable weight-deconfliction heuristic. It is not a Bayesian network, causal model, conditional-probability calculation, scenario model, calibration result, or forecasting-quality claim.

## Inputs and validation

The method receives:

- the complete approved Forecast Graph;
- the exact subset of `ForecastNodeRun` records that passed the critical-node, three-node, evidence-sufficiency, and material-node gates.

It fails closed for an empty graph, duplicate graph node IDs, unknown or self-referential parents or dependencies, missing or invalid weights, zero total graph importance, duplicate or outside node forecasts, mixed Forecast Run IDs, and non-finite probabilities or probabilities outside `(0, 1)`. It never creates a probability for an omitted node.

## Direct relationship allocation

Let `G` be all graph nodes, `I` the included forecast nodes, `w_i` the canonical importance weight of node `i`, and `T = sum(w_i for i in G)`.

For every included source node `i`, form the deduplicated direct recipient set:

```text
R_i = {i, parent_node_id when present, every direct dependency ID}
share_i = w_i / |R_i|
```

The source weight is split equally, with no learned coefficient:

- a share addressed to an included recipient adds to that recipient's effective weight;
- a share addressed to an excluded recipient adds to neutral residual weight;
- the full raw weight of every excluded graph node also adds to neutral residual weight.

A recipient named both as parent and dependency is counted once. Only direct relationships are used; no transitive closure is calculated. Graph-node order, dependency-list order, and node-run order do not affect the result.

## Conservation and neutral residual

All mass is represented by the invariant:

```text
sum(effective included weights) + neutral residual weight = T
```

Weights and split shares use `Decimal` values constructed from canonical string representations. If a recurring decimal split creates a precision remainder, the trace records the adjustment. A remainder is added to neutral mass when unrepresented mass already exists; otherwise it is assigned deterministically to an included recipient so a fully represented graph does not fabricate neutral residual.

For each included node `j`:

```text
normalized_effective_weight_j = effective_weight_j / T
logit_j = ln(p_j / (1 - p_j))
```

Neutral residual uses probability `0.5`, whose log odds are `0`. The combined result is:

```text
L = sum(normalized_effective_weight_j * logit_j for j in I)
final_probability = sigmoid(L)
```

Included effective weights are never renormalized to one when residual mass exists. The residual therefore shrinks the aggregate toward `0.5` without imputing a missing probability or reallocating omitted mass to surviving forecasts.

When every graph node is included and no parent or dependency relationships exist, the calculation matches `importance_weighted_log_odds_v1` at ForecastLab's established rounding precision.

## Audit record

The immutable `ForecastAggregation` trace records:

- method, policy, direct-only rule, neutral probability, and no-imputation rule;
- total, included, excluded, effective, and neutral graph mass plus the exact conservation result;
- every included source node's raw weight, direct recipients, equal share, included and excluded recipients, and residual allocation;
- every excluded node's raw weight, exclusion origin, and neutral allocation;
- every included forecast node's self-originated mass, relationship mass received, relationship sources, effective and normalized weights, input probability, log odds, and weighted contribution;
- combined log odds and final probability;
- the passing Evidence Sufficiency and Material Node Coverage assessment identities and hashes.

The allocation hash excludes probabilities, reasoning, outcomes, evidence confidence, and source-quality judgments. Changing weights or declared direct relationships changes the allocation. Raw model responses, hidden reasoning, prompts, headers, and credentials are never part of this audit.

## Profile separation and limits

`graph_forecaster_v1` version 8 selects this method only after both private-V1 deterministic gates pass. Existing profile snapshots and `graph_live_smoke_v1` version 3 continue to use `importance_weighted_log_odds_v1` unchanged.

The method does not validate whether graph-authored importance weights or relationships are substantively correct. It does not model relationship strength, conditional dependence, common causes, transitive effects, or scenario overlap. Scenario synthesis, calibration, frozen real-data evaluation, and forecasting-quality validation remain future work.
