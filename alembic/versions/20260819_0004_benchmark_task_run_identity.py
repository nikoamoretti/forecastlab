"""One BenchmarkTask to one ForecastRun, plus terminal-state columns.

Revision ID: 20260819_0004
Revises: 20260819_0003
Create Date: 2026-08-19

Inspect-and-alter. A no-op when current metadata already has these columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260819_0004"
down_revision = "20260819_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    _add_missing_columns()
    _install_run_task_unique(bind)


def downgrade() -> None:
    raise NotImplementedError("Benchmark task identity revision 20260819_0004 is not reversible.")


def _inspector(bind):
    return inspect(bind)


def _columns(bind, table: str) -> set[str]:
    if table not in _inspector(bind).get_table_names():
        return set()
    return {column["name"] for column in _inspector(bind).get_columns(table)}


def _has_unique(bind, table: str, columns: set[str]) -> bool:
    inspector = _inspector(bind)
    if table not in inspector.get_table_names():
        return False
    for item in inspector.get_unique_constraints(table):
        if set(item.get("column_names") or []) == columns:
            return True
    for item in inspector.get_indexes(table):
        if item.get("unique") and set(item.get("column_names") or []) == columns:
            return True
    return False


def _add_column(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if table not in _inspector(bind).get_table_names():
        return
    if column.name in _columns(bind, table):
        return
    op.add_column(table, column)


def _add_missing_columns() -> None:
    _add_column("forecast_runs", sa.Column("benchmark_task_id", sa.String(36), nullable=True))
    _add_column("benchmark_tasks", sa.Column("question_id", sa.String(36), nullable=True))
    _add_column("benchmark_tasks", sa.Column("error_category", sa.String(64), nullable=True))
    _add_column("benchmark_results", sa.Column("partial", sa.Boolean(), nullable=False, server_default=sa.false()))


def _install_run_task_unique(bind) -> None:
    if "forecast_runs" not in _inspector(bind).get_table_names():
        return
    if "benchmark_task_id" not in _columns(bind, "forecast_runs"):
        return
    if _has_unique(bind, "forecast_runs", {"benchmark_task_id"}):
        return
    with op.batch_alter_table("forecast_runs") as batch:
        batch.create_unique_constraint("uq_forecast_run_benchmark_task", ["benchmark_task_id"])
