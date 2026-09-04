"""Personal forecast envelopes and prospective cohorts (additive)."""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0031"
down_revision = "20260831_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if {"personal_forecasts", "prospective_cohorts", "prospective_entries", "prospective_assignments", "prospective_outcomes"}.issubset(tables):
        _outcome_triggers()
        return
    op.create_table("personal_forecasts",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("forecast_runs.id"), primary_key=True),
        sa.Column("request_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("forecast_contracts.id")),
        sa.Column("contract_json", sa.Text(), nullable=False),
        sa.Column("profile_json", sa.Text(), nullable=False),
        sa.Column("prompts_json", sa.Text(), nullable=False),
        sa.Column("macro_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("outcome_status", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("prospective_cohorts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("budget_usd", sa.Float(), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("manifest_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True)))
    op.create_table("prospective_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cohort_id", sa.String(36), sa.ForeignKey("prospective_cohorts.id"), nullable=False),
        sa.Column("question_id", sa.String(36), sa.ForeignKey("questions.id"), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=False),
        sa.Column("macro_json", sa.Text(), nullable=False),
        sa.Column("release_event", sa.String(255), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cohort_id", "question_id", name="uq_prospective_entry"))
    op.create_table("prospective_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cohort_id", sa.String(36), sa.ForeignKey("prospective_cohorts.id"), nullable=False),
        sa.Column("entry_id", sa.String(36), sa.ForeignKey("prospective_entries.id"), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("forecast_runs.id"), nullable=False, unique=True),
        sa.UniqueConstraint("entry_id", "profile_id", name="uq_prospective_assignment"))
    op.create_table("prospective_outcomes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entry_id", sa.String(36), sa.ForeignKey("prospective_entries.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Integer()),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entry_id", "revision", name="uq_prospective_outcome_revision"),
        sa.CheckConstraint("outcome IS NULL OR outcome IN (0, 1)", name="ck_prospective_outcome_binary"))
    # Protect adjudications even when SQL is issued outside the ORM.
    _outcome_triggers()


def _outcome_triggers() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER IF NOT EXISTS prospective_outcome_no_{action.lower()} BEFORE {action} ON prospective_outcomes BEGIN SELECT RAISE(ABORT, 'prospective_outcomes_are_append_only'); END")


def downgrade() -> None:
    tables = ("prospective_outcomes", "prospective_assignments", "prospective_entries", "prospective_cohorts", "personal_forecasts")
    for table in tables:
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one():
            raise RuntimeError("Personal forecast history is retained; restore a verified backup to roll back this migration")
    for table in tables:
        op.drop_table(table)
