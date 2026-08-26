# Material Node Completeness Policy V1

`private_v1_material_node_gate_v1` is ForecastLab's deterministic guard against producing a private-V1 probability after omitting a strictly higher-importance graph uncertainty while retaining lower-importance nodes. It is an execution-integrity rule, not a forecasting-quality, calibration, or accuracy claim.

## Canonical input

The policy uses only `ForecastNode.importance_weight` from the approved Forecast Graph. Each value is converted to `Decimal` from its canonical string representation before comparison or summation. Binary floating-point epsilon rules are not used.

The evaluator does not consume Research Planner priority, node probability, reasoning, confidence, evidence confidence, source quality, provider output, or resolved outcomes. It does not change graph generation, Research Planner scoring, node selection, node forecasts, or aggregation weights.

## Plan frontier

The plan audit runs after the ordinary Research Plan is frozen and before node research:

1. `selected_frontier_weight` is the minimum canonical importance weight among selected nodes.
2. A skipped graph node is a higher-importance omission only when its weight is strictly greater than that frontier.
3. The plan passes only when no higher-importance skipped node exists.

An omitted node whose weight equals the selected frontier is allowed and recorded as a warning. The evaluator does not reorder, replace, or add selected nodes. A failing audit remains embedded in `ResearchPlan.budget_allocation.material_node_plan_audit`, and execution stops before node-research model calls, searches, fetches, Evidence Claims, node forecasts, evidence assessment, aggregation, or Forecast Version creation.

The structured plan failure is:

- stage: `research_planning`
- error code: `private_v1_plan_omits_higher_importance_node`

## Execution frontier

The execution assessment runs after research, node forecasting, the critical-node and three-successful-node checks, and the Evidence Sufficiency Assessment:

1. The included set is exactly the successful node forecasts proposed for aggregation.
2. `included_frontier_weight` is the minimum canonical importance weight among included nodes.
3. Every graph node not included is excluded.
4. An excluded node fails the assessment only when its weight is strictly greater than the included frontier.

An equal-weight excluded node is allowed and recorded as a warning. The evaluator neither imputes a missing forecast nor changes the included set, probabilities, raw weights, normalized weights, or aggregation calculation. A failing assessment preserves the Research Plan, evidence, node runs, evidence assessment, and material assessment while creating no Forecast Aggregation, Forecast Version, or final probability.

The structured execution failure is:

- stage: `material_node_coverage`
- error code: `material_node_coverage_gate_failed`

The existing evidence rule requiring at least 50 percent canonical graph-weight coverage remains separate and unchanged. Both configured gates must pass.

## Immutable audit

The plan audit freezes selected/skipped IDs and weights, both frontier values, higher-weight omissions, equal-weight ties, stable reasons and warnings, the complete policy snapshot, and an input hash.

One `MaterialNodeCoverageAssessment` is permitted per Forecast Run. It stores the graph and plan identities, evidence-assessment identity and hash, graph/selected/included/excluded counts and weights, frontiers, higher-weight omissions, equal-weight ties, inclusion-affecting failure identities, relationship warnings, policy snapshot, and deterministic input hash. The hash intentionally excludes probabilities, reasoning, resolved outcomes, evidence confidence, and source quality.

Repeating an identical assessment returns the existing record. A different input hash for an already-assessed run is a persistence-integrity failure and never overwrites the original assessment.

## Relationship warnings

For every included node, ForecastLab records whether its declared parent or dependency is excluded. These findings are warnings only for this gate. They do not block execution, modify the included set, change a probability, or change the materiality result.

The separate `relationship_mass_conserving_log_odds_v1` aggregator consumes direct relationships after this gate passes and sends unrepresented shares to neutral residual mass. That deterministic deconfliction does not change the gate and is not hierarchical or conditional-probability inference.

## Profile separation

`graph_forecaster_v1` version 8 selects this policy, retains `private_v1_evidence_gate_v1`, and selects the separate relationship-aware aggregation method. `graph_live_smoke_v1` version 3 remains byte-for-byte unchanged, resolves `material_node_policy` to `none`, retains the importance-only aggregation method, and preserves its prior operational-smoke behavior. Existing frozen run and experiment profile snapshots are not rewritten.

Passing this policy means only that the strict materiality frontier was respected. It does not establish that graph weights are correct, evidence is factually true, the forecast is calibrated, dependencies were modeled, or forecasting quality improved.
