"""Append-only official macro outcome amendments."""

from alembic import op
from sqlalchemy import inspect, text

revision = "20260922_0033"
down_revision = "20260904_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from forecastlab_api.autopilot_models import OfficialMacroOutcomeAmendment

    OfficialMacroOutcomeAmendment.__table__.create(op.get_bind(), checkfirst=True)
    table = "official_macro_outcome_amendments"
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_immutable_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append_only_record'); END")
    else:
        op.execute("CREATE OR REPLACE FUNCTION forecastlab_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append_only_record'; END; $$")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION forecastlab_append_only()")


def downgrade() -> None:
    table = "official_macro_outcome_amendments"
    if not inspect(op.get_bind()).has_table(table):
        return
    if op.get_bind().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar():
        raise RuntimeError("Official outcome amendment history is retained; restore a verified backup to roll back")
    if op.get_bind().dialect.name == "sqlite":
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_{action}")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.drop_table(table)
