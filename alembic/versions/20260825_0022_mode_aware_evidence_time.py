"""Add mode-aware evidence temporal provenance.

Revision ID: 20260825_0022
Revises: 20260824_0021
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260825_0022"
down_revision = "20260824_0021"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    required = {"forecast_runs", "evidence_items", "evidence_claims"}
    if not required <= tables:
        missing = ",".join(sorted(required - tables))
        raise RuntimeError(f"temporal provenance prerequisite tables missing:{missing}")

    item_columns = _column_names("evidence_items")
    item_additions = (
        sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temporal_basis", sa.String(length=32), nullable=True),
        sa.Column("publication_date_source", sa.String(length=128), nullable=True),
        sa.Column("publication_date_verified", sa.Boolean(), nullable=True),
        sa.Column("publication_date_hint", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_date_hint_source", sa.String(length=128), nullable=True),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified_date_source", sa.String(length=128), nullable=True),
    )
    for column in item_additions:
        if column.name not in item_columns:
            op.add_column("evidence_items", column)

    op.execute(
        sa.text(
            """
            UPDATE evidence_items
            SET source_available_at = CASE
                    WHEN (SELECT mode FROM forecast_runs WHERE forecast_runs.id = evidence_items.run_id) = 'backtest'
                         AND snapshot_verification_status IN ('verified', 'fixture', 'cutoff_consistent_mock_manifest_verified')
                         AND COALESCE(final_snapshot_at, snapshot_at) IS NOT NULL
                        THEN COALESCE(final_snapshot_at, snapshot_at)
                    WHEN (SELECT mode FROM forecast_runs WHERE forecast_runs.id = evidence_items.run_id) = 'demo'
                         AND snapshot_verification_status = 'fixture'
                         AND COALESCE(final_snapshot_at, snapshot_at, published_at) IS NOT NULL
                        THEN COALESCE(final_snapshot_at, snapshot_at, published_at)
                    WHEN (SELECT mode FROM forecast_runs WHERE forecast_runs.id = evidence_items.run_id) = 'live'
                        THEN retrieved_at
                    WHEN published_at IS NOT NULL
                        THEN published_at
                    ELSE retrieved_at
                END,
                temporal_basis = CASE
                    WHEN (SELECT mode FROM forecast_runs WHERE forecast_runs.id = evidence_items.run_id) = 'backtest'
                         AND snapshot_verification_status IN ('verified', 'fixture', 'cutoff_consistent_mock_manifest_verified')
                         AND COALESCE(final_snapshot_at, snapshot_at) IS NOT NULL
                        THEN 'snapshot_date'
                    WHEN (SELECT mode FROM forecast_runs WHERE forecast_runs.id = evidence_items.run_id) = 'demo'
                         AND snapshot_verification_status = 'fixture'
                         AND COALESCE(final_snapshot_at, snapshot_at) IS NOT NULL
                        THEN 'snapshot_date'
                    WHEN (SELECT mode FROM forecast_runs WHERE forecast_runs.id = evidence_items.run_id) = 'live'
                        THEN 'retrieval_date'
                    WHEN published_at IS NOT NULL
                        THEN 'publication_date'
                    ELSE 'retrieval_date'
                END,
                publication_date_source = CASE
                    WHEN published_at IS NULL THEN NULL
                    WHEN snapshot_verification_status IN ('fixture', 'cutoff_consistent_mock_manifest_verified')
                        THEN 'legacy_fixture_metadata'
                    ELSE 'legacy_unverified_published_at'
                END,
                publication_date_verified = CASE
                    WHEN published_at IS NOT NULL
                         AND snapshot_verification_status IN ('fixture', 'cutoff_consistent_mock_manifest_verified')
                        THEN TRUE
                    ELSE FALSE
                END
            """
        )
    )

    claim_columns = _column_names("evidence_claims")
    claim_additions = (
        sa.Column("publication_date_source", sa.String(length=128), nullable=True),
        sa.Column("publication_date_verified", sa.Boolean(), nullable=True),
        sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temporal_basis", sa.String(length=32), nullable=True),
    )
    for column in claim_additions:
        if column.name not in claim_columns:
            op.add_column("evidence_claims", column)

    op.execute(
        sa.text(
            """
            UPDATE evidence_claims
            SET publication_date_source = COALESCE(
                    (SELECT publication_date_source FROM evidence_items
                     WHERE evidence_items.id = evidence_claims.evidence_item_id),
                    CASE WHEN publication_date IS NOT NULL THEN 'legacy_unverified_published_at' ELSE NULL END
                ),
                publication_date_verified = COALESCE(
                    (SELECT publication_date_verified FROM evidence_items
                     WHERE evidence_items.id = evidence_claims.evidence_item_id),
                    FALSE
                ),
                source_available_at = COALESCE(
                    (SELECT source_available_at FROM evidence_items
                     WHERE evidence_items.id = evidence_claims.evidence_item_id),
                    retrieval_date
                ),
                temporal_basis = COALESCE(
                    (SELECT temporal_basis FROM evidence_items
                     WHERE evidence_items.id = evidence_claims.evidence_item_id),
                    CASE WHEN publication_date IS NOT NULL THEN 'publication_date' ELSE 'retrieval_date' END
                )
            """
        )
    )

    with op.batch_alter_table("evidence_items") as batch:
        batch.alter_column("source_available_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column("temporal_basis", existing_type=sa.String(length=32), nullable=False)
        batch.alter_column("publication_date_verified", existing_type=sa.Boolean(), nullable=False)

    with op.batch_alter_table("evidence_claims") as batch:
        batch.alter_column(
            "publication_date",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch.alter_column("publication_date_verified", existing_type=sa.Boolean(), nullable=False)
        batch.alter_column("source_available_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.alter_column("temporal_basis", existing_type=sa.String(length=32), nullable=False)


def downgrade() -> None:
    raise NotImplementedError(
        "Mode-aware evidence temporal provenance migration 20260825_0022 is not reversible."
    )
