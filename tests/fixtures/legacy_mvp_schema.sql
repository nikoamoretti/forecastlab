-- Original ForecastLab MVP schema at 2caa33abc0cbd3e2c379de0763f13878839b3b6e.
-- Hand-written from that commit's models. Do not generate this from current metadata.

CREATE TABLE questions (
    id VARCHAR(36) NOT NULL,
    original_text TEXT NOT NULL,
    normalized_text TEXT,
    question_type VARCHAR(32) NOT NULL,
    created_at DATETIME NOT NULL,
    forecast_deadline DATETIME,
    status VARCHAR(32) NOT NULL,
    notes TEXT,
    stale BOOLEAN NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE resolution_contracts (
    id VARCHAR(36) NOT NULL,
    question_id VARCHAR(36) NOT NULL,
    exact_yes TEXT NOT NULL,
    exact_no TEXT NOT NULL,
    resolution_deadline DATETIME NOT NULL,
    authoritative_source TEXT NOT NULL,
    fallback_sources_json TEXT NOT NULL,
    geography VARCHAR(128),
    units VARCHAR(128),
    ambiguity_notes TEXT NOT NULL,
    cancellation_conditions TEXT NOT NULL,
    resolver_risk_notes TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(question_id) REFERENCES questions (id)
);

CREATE TABLE forecast_runs (
    id VARCHAR(36) NOT NULL,
    question_id VARCHAR(36) NOT NULL,
    profile_id VARCHAR(64) NOT NULL,
    mode VARCHAR(32) NOT NULL,
    as_of DATETIME,
    started_at DATETIME,
    finished_at DATETIME,
    job_id VARCHAR(36),
    status VARCHAR(32) NOT NULL,
    cost_usd FLOAT NOT NULL,
    tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    error_message TEXT,
    error_stage VARCHAR(64),
    provider_json TEXT NOT NULL,
    prompt_versions_json TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    aggregation_json TEXT NOT NULL,
    disagreement_summary TEXT,
    progress_pct FLOAT NOT NULL,
    progress_stage VARCHAR(64) NOT NULL,
    progress_message TEXT NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(question_id) REFERENCES questions (id)
);

CREATE TABLE research_tracks (
    id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    track_type VARCHAR(32) NOT NULL,
    plan_json TEXT NOT NULL,
    probability FLOAT,
    prior_probability FLOAT,
    reasoning_summary TEXT,
    key_drivers_json TEXT NOT NULL,
    counterarguments_json TEXT NOT NULL,
    unresolved_json TEXT NOT NULL,
    resolver_risk FLOAT,
    evidence_quality FLOAT,
    status VARCHAR(32) NOT NULL,
    error_message TEXT,
    independent BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(run_id) REFERENCES forecast_runs (id)
);

CREATE TABLE subquestions (
    id VARCHAR(36) NOT NULL,
    track_id VARCHAR(36) NOT NULL,
    text TEXT NOT NULL,
    purpose TEXT NOT NULL,
    preferred_source_types_json TEXT NOT NULL,
    search_queries_json TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    relationship_to_forecast TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(track_id) REFERENCES research_tracks (id)
);

CREATE TABLE evidence_items (
    id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    track_id VARCHAR(36),
    subquestion TEXT,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    publisher VARCHAR(256),
    published_at DATETIME,
    retrieved_at DATETIME NOT NULL,
    excerpt TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    source_class VARCHAR(32) NOT NULL,
    as_of_eligible BOOLEAN NOT NULL,
    rejected BOOLEAN NOT NULL,
    rejection_reason TEXT,
    snapshot_url TEXT,
    snapshot_at DATETIME,
    status_code INTEGER NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(run_id) REFERENCES forecast_runs (id)
);

CREATE TABLE forecast_versions (
    id VARCHAR(36) NOT NULL,
    question_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    raw_track_probabilities_json TEXT NOT NULL,
    ensemble_probability FLOAT,
    aggregation_json TEXT NOT NULL,
    shrinkage FLOAT NOT NULL,
    track_spread FLOAT,
    key_drivers_json TEXT NOT NULL,
    counterarguments_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    trigger_event VARCHAR(64) NOT NULL,
    previous_version_id VARCHAR(36),
    PRIMARY KEY (id),
    FOREIGN KEY(question_id) REFERENCES questions (id),
    FOREIGN KEY(run_id) REFERENCES forecast_runs (id)
);

CREATE TABLE jobs (
    id VARCHAR(36) NOT NULL,
    job_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload_json TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    error TEXT,
    created_at DATETIME NOT NULL,
    started_at DATETIME,
    finished_at DATETIME,
    heartbeat_at DATETIME,
    progress_stage VARCHAR(64) NOT NULL,
    progress_message TEXT NOT NULL,
    progress_pct FLOAT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_jobs_idempotency UNIQUE (idempotency_key)
);

CREATE TABLE job_events (
    id VARCHAR(36) NOT NULL,
    job_id VARCHAR(36) NOT NULL,
    created_at DATETIME NOT NULL,
    stage VARCHAR(64) NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(job_id) REFERENCES jobs (id)
);

CREATE TABLE benchmark_questions (
    id VARCHAR(36) NOT NULL,
    question TEXT NOT NULL,
    forecast_date DATETIME NOT NULL,
    resolution_date DATETIME NOT NULL,
    outcome INTEGER NOT NULL,
    resolution_source TEXT NOT NULL,
    category VARCHAR(64) NOT NULL,
    provenance VARCHAR(128) NOT NULL,
    import_hash VARCHAR(64) NOT NULL,
    is_synthetic BOOLEAN NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_benchmark_import_hash UNIQUE (import_hash)
);

CREATE TABLE benchmark_results (
    id VARCHAR(36) NOT NULL,
    benchmark_question_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36),
    profile_id VARCHAR(64) NOT NULL,
    probability FLOAT,
    brier FLOAT,
    log_loss_value FLOAT,
    cost_usd FLOAT NOT NULL,
    latency_ms INTEGER NOT NULL,
    failed BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(benchmark_question_id) REFERENCES benchmark_questions (id)
);

CREATE TABLE watches (
    id VARCHAR(36) NOT NULL,
    question_id VARCHAR(36) NOT NULL,
    endpoint_url TEXT NOT NULL,
    endpoint_type VARCHAR(16) NOT NULL,
    json_path VARCHAR(256),
    poll_seconds INTEGER NOT NULL,
    previous_hash VARCHAR(64),
    previous_value TEXT,
    status VARCHAR(32) NOT NULL,
    auto_rerun BOOLEAN NOT NULL,
    last_checked_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(question_id) REFERENCES questions (id)
);

CREATE TABLE watch_events (
    id VARCHAR(36) NOT NULL,
    watch_id VARCHAR(36) NOT NULL,
    old_hash VARCHAR(64),
    new_hash VARCHAR(64),
    old_value TEXT,
    new_value TEXT,
    created_at DATETIME NOT NULL,
    material BOOLEAN NOT NULL,
    resulting_version_id VARCHAR(36),
    fetch_status VARCHAR(32) NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(watch_id) REFERENCES watches (id)
);

CREATE TABLE worker_heartbeats (
    id VARCHAR(32) NOT NULL,
    last_seen_at DATETIME NOT NULL,
    status VARCHAR(32) NOT NULL,
    PRIMARY KEY (id)
);
