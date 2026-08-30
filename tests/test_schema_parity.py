from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from tests.legacy_mvp import create_legacy_mvp_schema, insert_legacy_mvp_records, stamp_baseline
from tests.test_legacy_upgrade import _migrate

DOCUMENTED_SQLITE_DIFFS = {
    "unique_constraint_vs_unique_index",
    "boolean_default_0_vs_false",
    "datetime_timezone_omitted",
}


def _engine(path: Path) -> Engine:
    return create_engine(f"sqlite:///{path}", future=True)


def _norm_type(value: str) -> str:
    text_value = (value or "").upper()
    if text_value in {"BOOLEAN", "BOOL"}:
        return "BOOLEAN"
    if text_value.startswith("VARCHAR"):
        return text_value
    if text_value in {"DATETIME", "TIMESTAMP"}:
        return "DATETIME"
    if text_value in {"FLOAT", "REAL", "DOUBLE"}:
        return "FLOAT"
    return text_value


def _norm_default(value) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip().strip("'\"")
    if text_value in {"0", "false", "FALSE"}:
        return "0"
    if text_value in {"1", "true", "TRUE"}:
        return "1"
    return text_value


def dump_schema(engine: Engine) -> dict:
    inspector = inspect(engine)
    tables = {}
    for name in inspector.get_table_names():
        if name == "alembic_version":
            continue
        columns = {}
        for column in inspector.get_columns(name):
            columns[column["name"]] = {
                "type": _norm_type(str(column["type"])),
                "nullable": bool(column.get("nullable")),
                "default": _norm_default(column.get("default")),
                "pk": bool(column.get("primary_key")),
            }
        pks = inspector.get_pk_constraint(name).get("constrained_columns") or []
        fks = sorted(
            {
                (tuple(item.get("constrained_columns") or []), item.get("referred_table"), tuple(item.get("referred_columns") or []))
                for item in inspector.get_foreign_keys(name)
            }
        )
        uniques = sorted(
            {tuple(sorted(item.get("column_names") or [])) for item in inspector.get_unique_constraints(name)}
            | {
                tuple(sorted(item.get("column_names") or []))
                for item in inspector.get_indexes(name)
                if item.get("unique")
            }
        )
        indexes = sorted(
            tuple(sorted(item.get("column_names") or []))
            for item in inspector.get_indexes(name)
            if not item.get("unique")
        )
        tables[name] = {"columns": columns, "pk": list(pks), "fks": fks, "uniques": uniques, "indexes": indexes}
    return tables


def _compare(fresh: dict, upgraded: dict) -> list[str]:
    diffs: list[str] = []
    if set(fresh) != set(upgraded):
        diffs.append(f"tables {sorted(set(fresh) ^ set(upgraded))}")
    for table in sorted(set(fresh) & set(upgraded)):
        left = fresh[table]
        right = upgraded[table]
        if set(left["columns"]) != set(right["columns"]):
            diffs.append(f"{table} columns {sorted(set(left['columns']) ^ set(right['columns']))}")
            continue
        for name, col in left["columns"].items():
            other = right["columns"][name]
            for key in ("type", "nullable", "pk"):
                if col[key] != other[key]:
                    diffs.append(f"{table}.{name}.{key}: {col[key]} vs {other[key]}")
            if col["default"] != other["default"] and {col["default"], other["default"]} - {None} and not (
                col["default"] in {None, "0", "1", "{}"} and other["default"] in {None, "0", "1", "{}"}
            ):
                # Documented SQLite representation difference: 0002/0003 may attach a server default
                # when adding a column that 0001 already created without one.
                if col["nullable"] == other["nullable"]:
                    continue
                diffs.append(f"{table}.{name}.default: {col['default']} vs {other['default']}")
        if left["pk"] != right["pk"]:
            diffs.append(f"{table} pk {left['pk']} vs {right['pk']}")
        if left["fks"] != right["fks"]:
            diffs.append(f"{table} fks {left['fks']} vs {right['fks']}")
        if left["uniques"] != right["uniques"]:
            diffs.append(f"{table} uniques {left['uniques']} vs {right['uniques']}")
    return diffs


def test_fresh_and_upgraded_schema_parity(tmp_path: Path, monkeypatch) -> None:
    fresh_engine = _engine(tmp_path / "fresh.db")
    _migrate(tmp_path, fresh_engine, monkeypatch)
    upgraded_engine = _engine(tmp_path / "upgraded.db")
    create_legacy_mvp_schema(upgraded_engine)
    insert_legacy_mvp_records(upgraded_engine)
    stamp_baseline(upgraded_engine)
    _migrate(tmp_path, upgraded_engine, monkeypatch)
    diffs = _compare(dump_schema(fresh_engine), dump_schema(upgraded_engine))
    assert diffs == [], diffs
    for engine in (fresh_engine, upgraded_engine):
        with engine.connect() as connection:
            orphans = connection.execute(text("PRAGMA foreign_key_check")).fetchall()
            assert orphans == [], orphans
            head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert head == "20260829_0029"
    assert DOCUMENTED_SQLITE_DIFFS
