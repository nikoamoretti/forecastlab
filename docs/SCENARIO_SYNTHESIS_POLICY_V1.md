# Scenario Synthesis Policy V1

Policy identifier: `private_v1_scenario_synthesis_v1`

Status: frozen private-V1 explanatory policy. This policy makes no forecasting-quality, calibration, or causal-inference claim.

## Purpose and execution position

Scenario Synthesis explains how already-included forecast nodes and already-cited eligible evidence could interact. It runs only after the critical-node and three-node rules, `private_v1_evidence_gate_v1`, and `private_v1_material_node_gate_v1` pass, and immediately before the unchanged relationship-aware aggregation.

The stage must produce exactly three grounded pathways:

- `base_case`
- `yes_case`
- `no_case`

These pathways are explanatory views, not mutually exclusive outcomes, scenario-probability buckets, additional node forecasts, or alternative aggregation inputs.

## Bounded canonical input

The synthesizer receives one deterministic canonical packet containing only:

- the approved contract's normalized question, yes and no conditions, resolution date, and resolver-risk notes;
- the approved graph identity;
- included node IDs and each included node's question, type, canonical importance, included parent, and included dependencies;
- exact included `ForecastNodeRun` probability, reasoning, uncertainty notes, and cited supporting/opposing claim IDs;
- only cited eligible claims, with claim and node IDs, claim text, exact excerpt, stance, source title, normalized host, deterministic source class, extraction method, temporal basis, and cutoff status;
- Evidence Sufficiency and Material Node assessment IDs and hashes.

The packet excludes the final aggregation or probability, resolved outcome, benchmark or external score, uncited or skipped evidence, excluded-node evidence, raw documents, provider responses, raw prompts, headers, credentials, and hidden reasoning. Its canonical serialization is input-hashed. Any change to an included run, cited claim, grounding relationship, or assessment identity changes that hash; outcome-only and external-score changes do not.

The packet must be no more than 30,000 characters and its estimated prompt use must fit the frozen 10,000-input-token reserve. ForecastLab fails before a provider request rather than truncating an oversized packet.

## Strict authored output

The strict structured output contains exactly three `ScenarioPathway` objects. Every object has only:

- `local_id`: nonempty, at most 32 characters;
- `kind`: `base_case`, `yes_case`, or `no_case`;
- `title`: nonempty, at most 96 characters;
- `summary`: nonempty, at most 600 characters;
- `node_ids`: two to eight unique included node IDs;
- `claim_ids`: one to sixteen unique cited eligible claim IDs;
- `mechanisms`: one to four strings, each at most 240 characters;
- `triggers`: one to four strings, each at most 200 characters;
- `invalidators`: one to four strings, each at most 200 characters;
- `unresolved_uncertainties`: one to four strings, each at most 200 characters.

Additional properties are forbidden. In particular, probability, weight, confidence, likelihood, adjustment, and raw-document fields are not part of the schema.

## Deterministic grounding and coverage

JSON Schema validation is necessary but not sufficient. After schema validation, code enforces all of the following:

- exactly one pathway of each required kind;
- unique local IDs and unique titles;
- at least two included nodes and at least one eligible cited claim per pathway;
- every referenced node belongs to the exact included aggregation set;
- every referenced claim was cited by an included node run and belongs to a node referenced by that pathway;
- every included aggregation node appears in at least one pathway;
- every direct parent/dependency relationship whose endpoints are both included is co-covered in at least one pathway;
- the yes-case and no-case node sets are not identical.

Skipped, excluded, wrong-node, rejected, temporally ineligible, or uncited claims are forbidden. Code assigns stable scenario IDs only after every rule passes. The model authors prose; code owns identifiers, grounding, coverage, persistence, and probability calculation.

## Frozen resource envelope

When this policy is enabled, Research Planner reserves the following before node research begins:

- one logical model call;
- zero semantic retries;
- 10,000 input tokens;
- 2,048 output tokens;
- minimal reasoning effort;
- low verbosity;
- a bounded 30-second stage allowance;
- the frozen catalog cost of that envelope.

Research, extraction, and node forecasting cannot consume the role-specific call or token reservation. Infeasible complete workflows fail before provider activity. An unused reserve is planning headroom, not provider usage, cost, or a ledger row.

OpenAI uses strict capability-gated JSON Schema output for this task. xAI and generic OpenAI-compatible providers do not receive unsupported OpenAI-specific schema, reasoning, or verbosity fields. Truncation, refusal, empty output, invalid JSON, schema mismatch, domain-grounding failure, provider failure, and budget failure are distinct sanitized outcomes. None triggers a repair prompt, second synthesis request, model switch, prose extraction, or silent fallback.

## Immutable persistence

ForecastLab stores one `ScenarioSynthesis` per Forecast Run with a unique run constraint. The artifact records:

- policy and prompt versions;
- success or failure status and timestamps;
- provider and model identities;
- deterministic input and output hashes;
- validated scenarios and coverage audit on success;
- stable failure reasons on failure;
- Evidence Sufficiency and Material Node assessment foreign keys;
- sanitized request diagnostics.

It never stores a raw provider response, raw prompt, hidden reasoning, authorization data, headers, or credentials. An identical input hash returns the existing artifact without another model request. A different input hash for the same run is a persistence-integrity failure and cannot overwrite history. Migration `20260826_0026` adds the table without fabricating scenario records for historical runs.

## Failure and success behavior

A synthesis failure preserves the contract, graph, Research Plan, evidence, node runs, Evidence Sufficiency Assessment, and Material Node Coverage Assessment. It records a graph execution failure at stage `scenario_synthesis` with code `scenario_synthesis_failed`, then creates no Forecast Aggregation, Forecast Version, or final probability.

On success, Scenario Synthesis identity, policy, input hash, and output hash enter execution and calculation audit traces. The executor then passes the exact same included node runs and approved graph to `relationship_mass_conserving_log_odds_v1`. Scenario content never changes included nodes, probabilities, canonical weights, direct relationships, allocation, neutral residual mass, combined log odds, or final probability.

## Profile separation

`graph_forecaster_v1` version 9 enables this policy and prompt `scenario_synthesis:v1` while retaining its version-8 evidence gate, material-node gate, relationship-aware aggregation, and all existing resource limits.

`graph_live_smoke_v1` remains byte-identical at version 3, resolves the default policy `none`, reserves no scenario capacity, creates no Scenario Synthesis artifact, and performs no synthesis call. Frozen older profile snapshots remain executable with their recorded behavior.

## Reporting and non-claims

JSON, Markdown, and web reports show the artifact status and hashes, three pathway texts, referenced nodes and claims, source provenance, mechanisms, triggers, invalidators, unresolved uncertainties, coverage audit, and sanitized diagnostics before the numerical calculation. Reports explicitly state that the pathways have no assigned probabilities and do not alter deterministic aggregation.

This policy does not establish that its prose is causally correct, exhaustive, calibrated, or useful for forecasting. It does not implement probabilistic scenario weights, conditional probability inference, scenario overlap mathematics, a Bayesian network, calibration, or a quality evaluation. Those require separate pre-registration and frozen real-data testing.
