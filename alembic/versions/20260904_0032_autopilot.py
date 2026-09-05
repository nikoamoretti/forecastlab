"""Cloud persistence, owner sessions, durable automation and adjudications."""
import sqlalchemy as sa
from alembic import op

revision = "20260904_0032"
down_revision = "20260904_0031"
branch_labels = None
depends_on = None


def upgrade():
    from forecastlab_api import models  # noqa: F401
    from forecastlab_api.autopilot_models import TABLES
    for model in TABLES:
        model.__table__.create(op.get_bind(), checkfirst=True)
    for table in ("question_adjudications", "outcome_proposals", "autopilot_policies", "autopilot_runs", "prospective_outcomes"):
        if op.get_bind().dialect.name == "sqlite":
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_immutable_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append_only_record'); END")
        else:
            op.execute("CREATE OR REPLACE FUNCTION forecastlab_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append_only_record'; END; $$")
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
            op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION forecastlab_append_only()")
    for key in ("scheduler", "paid_worker", "source_cache"):
        op.get_bind().execute(sa.text("INSERT INTO execution_leases (key, generation) VALUES (:key, 0) ON CONFLICT (key) DO NOTHING"), {"key": key})
    op.get_bind().execute(sa.text("INSERT INTO autopilot_state (id, enabled, qualification_enabled, pause_reason, provider_failures, restore_receipt_json) VALUES ('personal', :enabled, :enabled, 'Not enabled', 0, '{}') ON CONFLICT (id) DO NOTHING"), {"enabled": False})


def downgrade():
    from forecastlab_api.autopilot_models import TABLES
    # Empty installations remain reversible for migration verification. Once an
    # automation record exists, rollback must retain its append-only history.
    for table in ("autopilot_policies", "autopilot_runs", "outcome_proposals", "question_adjudications"):
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("Autopilot history is retained; restore a verified backup to roll back")
    if op.get_bind().dialect.name == "sqlite":
        for action in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS prospective_outcomes_immutable_{action}")
    else:
        op.execute("DROP TRIGGER IF EXISTS prospective_outcomes_immutable ON prospective_outcomes")
    for model in reversed(TABLES):
        model.__table__.drop(op.get_bind())
    if op.get_bind().dialect.name != "sqlite":
        op.execute("DROP FUNCTION IF EXISTS forecastlab_append_only()")
