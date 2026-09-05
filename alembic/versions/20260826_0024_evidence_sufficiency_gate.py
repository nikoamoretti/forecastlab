"""Add deterministic evidence provenance and immutable sufficiency assessments.

Revision ID: 20260826_0024
Revises: 20260825_0023
Create Date: 2026-08-26
"""

from __future__ import annotations

from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260826_0024"
down_revision = "20260825_0023"
branch_labels = None
depends_on = None


def _source_host(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if not {"evidence_claims", "evidence_items", "forecast_runs"}.issubset(tables):
        raise RuntimeError("evidence sufficiency prerequisite tables missing")

    claim_columns = {
        column["name"] for column in inspector.get_columns("evidence_claims")
    }
    if "source_class" not in claim_columns:
        op.add_column(
            "evidence_claims",
            sa.Column("source_class", sa.String(length=32), nullable=True),
        )
    if "extraction_method" not in claim_columns:
        op.add_column(
            "evidence_claims",
            sa.Column("extraction_method", sa.String(length=48), nullable=True),
        )
    if "source_host" not in claim_columns:
        op.add_column(
            "evidence_claims",
            sa.Column("source_host", sa.String(length=255), nullable=True),
        )

    rows = bind.execute(
        sa.text(
            """
            SELECT c.id, c.source_url, i.source_class AS item_source_class
            FROM evidence_claims AS c
            LEFT JOIN evidence_items AS i ON i.id = c.evidence_item_id
            """
        )
    ).mappings()
    for row in rows:
        item_source_class = row["item_source_class"]
        source_class = (
            item_source_class
            if item_source_class in {"primary", "secondary"}
            else "unknown_legacy"
        )
        bind.execute(
            sa.text(
                """
                UPDATE evidence_claims
                SET source_class = :source_class,
                    extraction_method = 'unknown_legacy',
                    source_host = :source_host
                WHERE id = :claim_id
                """
            ),
            {
                "claim_id": row["id"],
                "source_class": source_class,
                "source_host": _source_host(row["source_url"] or ""),
            },
        )

    with op.batch_alter_table("evidence_claims") as batch:
        batch.alter_column(
            "source_class",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="unknown_legacy",
        )
        batch.alter_column(
            "extraction_method",
            existing_type=sa.String(length=48),
            nullable=False,
            server_default="unknown_legacy",
        )
        batch.alter_column(
            "source_host",
            existing_type=sa.String(length=255),
            nullable=False,
            server_default="",
        )

    if "evidence_sufficiency_assessments" not in tables:
        op.create_table(
            "evidence_sufficiency_assessments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("forecast_run_id", sa.String(length=36), nullable=False),
            sa.Column("policy_version", sa.String(length=64), nullable=False),
            sa.Column("policy_snapshot_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("reasons_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("warnings_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("selected_node_count", sa.Integer(), nullable=False),
            sa.Column("included_node_count", sa.Integer(), nullable=False),
            sa.Column("critical_node_count", sa.Integer(), nullable=False),
            sa.Column("selected_coverage_numerator", sa.Integer(), nullable=False),
            sa.Column("selected_coverage_denominator", sa.Integer(), nullable=False),
            sa.Column("selected_node_coverage", sa.Float(), nullable=False),
            sa.Column("graph_coverage_numerator", sa.Integer(), nullable=False),
            sa.Column("graph_coverage_denominator", sa.Integer(), nullable=False),
            sa.Column("graph_node_coverage", sa.Float(), nullable=False),
            sa.Column("included_graph_weight", sa.Float(), nullable=False),
            sa.Column("total_graph_weight", sa.Float(), nullable=False),
            sa.Column("graph_weight_coverage", sa.Float(), nullable=False),
            sa.Column("cited_claim_count", sa.Integer(), nullable=False),
            sa.Column("cited_item_count", sa.Integer(), nullable=False),
            sa.Column("cited_source_count", sa.Integer(), nullable=False),
            sa.Column("distinct_host_count", sa.Integer(), nullable=False),
            sa.Column("distinct_hosts_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("primary_claim_count", sa.Integer(), nullable=False),
            sa.Column("primary_node_count", sa.Integer(), nullable=False),
            sa.Column("structured_claim_count", sa.Integer(), nullable=False),
            sa.Column("fallback_claim_count", sa.Integer(), nullable=False),
            sa.Column("included_node_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("excluded_node_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("insufficient_node_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("per_node_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("assessment_input_hash", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "status IN ('passed', 'failed')",
                name="ck_evidence_sufficiency_assessment_status",
            ),
            sa.ForeignKeyConstraint(["forecast_run_id"], ["forecast_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "forecast_run_id",
                name="uq_evidence_sufficiency_assessment_run",
            ),
        )


def downgrade() -> None:
    raise NotImplementedError(
        "Evidence sufficiency migration 20260826_0024 is not reversible."
    )
