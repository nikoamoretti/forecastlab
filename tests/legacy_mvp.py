from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

LEGACY_MVP_COMMIT = "2caa33abc0cbd3e2c379de0763f13878839b3b6e"
LEGACY_SCHEMA_SQL = Path(__file__).parent / "fixtures" / "legacy_mvp_schema.sql"

QUESTION_ID = "11111111-1111-1111-1111-111111111111"
CONTRACT_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
TRACK_BASE_ID = "44444444-4444-4444-4444-444444444441"
TRACK_CURRENT_ID = "44444444-4444-4444-4444-444444444442"
TRACK_SKEPTIC_ID = "44444444-4444-4444-4444-444444444443"
EVIDENCE_DATED_ID = "55555555-5555-5555-5555-555555555551"
EVIDENCE_UNDATED_ID = "55555555-5555-5555-5555-555555555552"
VERSION_ID = "66666666-6666-6666-6666-666666666666"
JOB_ID = "77777777-7777-7777-7777-777777777777"
BENCH_Q_ID = "88888888-8888-8888-8888-888888888888"
BENCH_RESULT_ID = "99999999-9999-9999-9999-999999999999"
WATCH_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

LEGACY_QUESTION = "Will legacy MVP data survive the integrity upgrade?"


def create_legacy_mvp_schema(engine: Engine) -> None:
    sql = LEGACY_SCHEMA_SQL.read_text(encoding="utf-8")
    with engine.begin() as connection:
        for statement in _split_sql(sql):
            connection.execute(text(statement))


def insert_legacy_mvp_records(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO questions (id, original_text, normalized_text, question_type, created_at,
                    forecast_deadline, status, notes, stale)
                VALUES (:id, :text, :text, 'binary', '2026-01-15 12:00:00', '2027-06-30 00:00:00',
                    'complete', 'legacy mvp row', 0)
                """
            ),
            {"id": QUESTION_ID, "text": LEGACY_QUESTION},
        )
        connection.execute(
            text(
                """
                INSERT INTO resolution_contracts (id, question_id, exact_yes, exact_no, resolution_deadline,
                    authoritative_source, fallback_sources_json, geography, units, ambiguity_notes,
                    cancellation_conditions, resolver_risk_notes, version, updated_at)
                VALUES (:id, :qid, 'Yes if official U-3 exceeds 5%.', 'No otherwise.',
                    '2027-07-15 00:00:00', 'https://www.bls.gov/news.release/empsit.nr0.htm',
                    '[]', 'US', 'percent', '', '', '', 1, '2026-01-15 12:05:00')
                """
            ),
            {"id": CONTRACT_ID, "qid": QUESTION_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO jobs (id, job_type, status, payload_json, attempts, max_attempts, idempotency_key,
                    error, created_at, started_at, finished_at, heartbeat_at, progress_stage, progress_message, progress_pct)
                VALUES (:id, 'forecast_run', 'completed', :payload, 1, 3, :idem,
                    NULL, '2026-01-15 12:10:00', '2026-01-15 12:10:01', '2026-01-15 12:10:08',
                    '2026-01-15 12:10:08', 'report', 'done', 1.0)
                """
            ),
            {"id": JOB_ID, "payload": f'{{"run_id": "{RUN_ID}"}}', "idem": f"run:{RUN_ID}"},
        )
        connection.execute(
            text(
                """
                INSERT INTO forecast_runs (id, question_id, profile_id, mode, as_of, started_at, finished_at,
                    job_id, status, cost_usd, tokens, latency_ms, error_message, error_stage, provider_json,
                    prompt_versions_json, budget_json, aggregation_json, disagreement_summary, progress_pct,
                    progress_stage, progress_message)
                VALUES (:id, :qid, 'three_track_ensemble', 'demo', NULL, '2026-01-15 12:10:01',
                    '2026-01-15 12:10:08', :job, 'completed', 0.0, 1200, 18, NULL, NULL, '{}', '{}', '{}',
                    '{"method":"equal_weight_logit_shrinkage"}', 'legacy disagreement', 1.0, 'report', 'done')
                """
            ),
            {"id": RUN_ID, "qid": QUESTION_ID, "job": JOB_ID},
        )
        for track_id, track_type, probability in (
            (TRACK_BASE_ID, "base_rate", 0.42),
            (TRACK_CURRENT_ID, "current_evidence", 0.31),
            (TRACK_SKEPTIC_ID, "skeptic", 0.38),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO research_tracks (id, run_id, track_type, plan_json, probability, prior_probability,
                        reasoning_summary, key_drivers_json, counterarguments_json, unresolved_json, resolver_risk,
                        evidence_quality, status, error_message, independent)
                    VALUES (:id, :run, :kind, '{}', :p, :p, :kind, '[]', '[]', '[]', 0.1, 0.7, 'completed', NULL, 1)
                    """
                ),
                {"id": track_id, "run": RUN_ID, "kind": track_type, "p": probability},
            )
        connection.execute(
            text(
                """
                INSERT INTO evidence_items (id, run_id, track_id, subquestion, url, title, publisher, published_at,
                    retrieved_at, excerpt, content_hash, source_class, as_of_eligible, rejected, rejection_reason,
                    snapshot_url, snapshot_at, status_code)
                VALUES (:id, :run, :track, 'rate', 'https://example.com/dated', 'Dated print', 'BLS',
                    '2025-12-01 00:00:00', '2026-01-15 12:10:03', '4.2 percent', 'abc', 'primary', 1, 0,
                    NULL, NULL, NULL, 200)
                """
            ),
            {"id": EVIDENCE_DATED_ID, "run": RUN_ID, "track": TRACK_CURRENT_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO evidence_items (id, run_id, track_id, subquestion, url, title, publisher, published_at,
                    retrieved_at, excerpt, content_hash, source_class, as_of_eligible, rejected, rejection_reason,
                    snapshot_url, snapshot_at, status_code)
                VALUES (:id, :run, :track, 'rate', 'https://example.com/undated', 'Undated note', 'Unknown',
                    NULL, '2026-01-15 12:10:04', 'no date', 'def', 'secondary', 1, 0, NULL, NULL, NULL, 200)
                """
            ),
            {"id": EVIDENCE_UNDATED_ID, "run": RUN_ID, "track": TRACK_SKEPTIC_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO forecast_versions (id, question_id, run_id, raw_track_probabilities_json,
                    ensemble_probability, aggregation_json, shrinkage, track_spread, key_drivers_json,
                    counterarguments_json, evidence_ids_json, created_at, trigger_event, previous_version_id)
                VALUES (:id, :qid, :run, :tracks, 0.374, '{}', 0.1, 0.11, '[]', '[]', '[]',
                    '2026-01-15 12:10:08', 'run', NULL)
                """
            ),
            {"id": VERSION_ID, "qid": QUESTION_ID, "run": RUN_ID, "tracks": '{"base_rate":0.42}'},
        )
        connection.execute(
            text(
                """
                INSERT INTO benchmark_questions (id, question, forecast_date, resolution_date, outcome,
                    resolution_source, category, provenance, import_hash, is_synthetic)
                VALUES (:id, 'Will a legacy benchmark row survive?', '2024-01-01 00:00:00',
                    '2024-06-01 00:00:00', 1, 'https://example.com/resolution', 'macro', 'user_import',
                    'legacy-import-hash-1', 0)
                """
            ),
            {"id": BENCH_Q_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO benchmark_results (id, benchmark_question_id, run_id, profile_id, probability,
                    brier, log_loss_value, cost_usd, latency_ms, failed, created_at)
                VALUES (:id, :bq, :run, 'three_track_ensemble', 0.6, 0.16, 0.51, 0.0, 20, 0,
                    '2026-01-15 12:20:00')
                """
            ),
            {"id": BENCH_RESULT_ID, "bq": BENCH_Q_ID, "run": RUN_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO watches (id, question_id, endpoint_url, endpoint_type, json_path, poll_seconds,
                    previous_hash, previous_value, status, auto_rerun, last_checked_at)
                VALUES (:id, :qid, 'http://127.0.0.1:8765/demo/indicators/unemployment', 'json', '$.value',
                    300, 'hash-1', '4.2', 'active', 0, '2026-01-15 12:30:00')
                """
            ),
            {"id": WATCH_ID, "qid": QUESTION_ID},
        )


def stamp_baseline(engine: Engine, revision: str = "20260818_0001") -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES (:rev)"), {"rev": revision})


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip(";").strip())
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return [item for item in statements if item]
