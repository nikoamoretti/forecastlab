"""Portable logical snapshots with full-row parity, private storage, and restore receipts."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta

from sqlalchemy import DateTime, inspect, select, text

from forecastlab.root_event import digest
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api import models  # noqa: F401
from forecastlab_api.artifact_store import get_bytes, put_bytes
from forecastlab_api.autopilot_models import AppSetting
from forecastlab_api.autopilot_store import claim_lease, notify, release_lease, state
from forecastlab_api.db import Base


def canonical(value):
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    return value


def snapshot_database(engine) -> dict:
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        else:
            connection.exec_driver_sql("BEGIN")
        tables = {}
        for name, table in sorted(Base.metadata.tables.items()):
            rows = connection.execute(select(table)).mappings().all()
            serialized = [{k: canonical(v) for k, v in row.items()} for row in rows]
            tables[name] = sorted(serialized, key=lambda r: json.dumps(r, sort_keys=True))
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        connection.rollback()
    payload = {"schema_version": "database_snapshot_v1", "alembic_revision": revision, "tables": tables}
    return {**payload, "sha256": digest(payload), "created_at": utcnow().isoformat()}


def verify_snapshot(snapshot: dict):
    if snapshot.get("schema_version") != "database_snapshot_v1" or digest({k: snapshot[k] for k in ("schema_version", "alembic_revision", "tables")}) != snapshot.get("sha256"):
        raise ValueError("backup_integrity_failed")
    if set(snapshot["tables"]) != set(Base.metadata.tables):
        raise ValueError("backup_schema_mismatch")


def restore_database(engine, snapshot: dict) -> dict:
    """Insert into a freshly migrated target or verify an identical previous import.

    Existing differing rows fail. Constraint deferral is transactional and is
    restored before commit; neither history nor IDs are rewritten.
    """
    verify_snapshot(snapshot)
    foreign_keys = []
    with engine.begin() as connection:
        actual_revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if actual_revision != snapshot["alembic_revision"]:
            raise ValueError("restore_migration_revision_mismatch")
        bootstrap = {"autopilot_state", "execution_leases"}
        empty = all(connection.execute(select(Base.metadata.tables[n]).limit(1)).first() is None
                    for n in snapshot["tables"] if n not in bootstrap)
        if empty:
            for name in bootstrap:
                connection.execute(Base.metadata.tables[name].delete())
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
        else:
            quote = connection.dialect.identifier_preparer.quote
            inspector = inspect(connection)
            for name in snapshot["tables"]:
                for fk in inspector.get_foreign_keys(name):
                    foreign_keys.append((name, fk["name"], fk.get("options", {})))
                    connection.exec_driver_sql(f"ALTER TABLE {quote(name)} ALTER CONSTRAINT {quote(fk['name'])} DEFERRABLE INITIALLY DEFERRED")
        for name, rows in snapshot["tables"].items():
            table = Base.metadata.tables[name]
            pk = [c.name for c in table.primary_key.columns]
            existing = {tuple(canonical(r[k]) for k in pk): {k: canonical(v) for k, v in r.items()}
                        for r in connection.execute(select(table)).mappings()}
            inserts = []
            for row in rows:
                key = tuple(row[k] for k in pk)
                if key in existing:
                    if existing[key] != row:
                        raise ValueError("restore_existing_record_differs:" + name)
                    continue
                converted = {k: datetime.fromisoformat(v) if isinstance(table.c[k].type, DateTime) and v is not None else v for k, v in row.items()}
                inserts.append(converted)
            for start in range(0, len(inserts), 100):
                connection.execute(table.insert(), inserts[start:start + 100])
        if engine.dialect.name == "postgresql":
            connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
            for name, fk, options in foreign_keys:
                timing = "DEFERRABLE INITIALLY DEFERRED" if options.get("initially") == "DEFERRED" else "DEFERRABLE INITIALLY IMMEDIATE" if options.get("deferrable") else "NOT DEFERRABLE"
                connection.exec_driver_sql(f"ALTER TABLE {quote(name)} ALTER CONSTRAINT {quote(fk)} {timing}")
        else:
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise ValueError("restore_foreign_key_check_failed")
    restored = snapshot_database(engine)
    if restored["sha256"] != snapshot["sha256"]:
        differing = [name for name in snapshot["tables"] if restored["tables"][name] != snapshot["tables"][name]]
        raise ValueError("restore_parity_failed:" + ",".join(differing))
    return {"verified": True, "snapshot_sha256": snapshot["sha256"], "verified_at": utcnow().isoformat(),
        "tables": {name: len(rows) for name, rows in snapshot["tables"].items()},
        "checks": ["all_row_hashes", "record_counts", "primary_keys", "contract_hashes", "ledger_totals", "export_payloads", "foreign_keys"]}


def daily_backup() -> dict:
    from forecastlab_api.config import settings
    from forecastlab_api.db import SessionLocal, engine
    if not settings.production:
        return {"status": "disabled_outside_production"}
    day = utcnow().date().isoformat()
    key = "backup:" + day
    with SessionLocal() as session:
        if session.get(AppSetting, key):
            return {"status": "already_saved"}
    ticket = claim_lease("database_backup", seconds=180)
    if ticket is None:
        return {"status": "already_running"}
    try:
        snapshot = snapshot_database(engine)
        artifact = put_bytes(gzip.compress(json.dumps(snapshot).encode()), content_type="application/gzip", prefix="backups")
        # Verify the remote bytes before declaring the snapshot available.
        verify_snapshot(json.loads(gzip.decompress(get_bytes(artifact))))
        with SessionLocal() as session:
            session.add(AppSetting(key=key, value_json=json.dumps({"artifact": artifact, "created_at": utcnow().isoformat(), "snapshot_sha256": snapshot["sha256"]})))
            session.commit()
            backups = session.scalars(select(AppSetting).where(AppSetting.key.like("backup:%")).order_by(AppSetting.key.desc())).all()
            # Retain at least seven complete daily restore points. If a day was
            # missed, keep older points until the seven-point floor is met.
            for old in backups[7:]:
                manifest = json.loads(old.value_json)
                if datetime.fromisoformat(manifest["created_at"]) > utcnow() - timedelta(days=7):
                    continue
                import os

                from vercel.blob import delete
                delete(manifest["artifact"]["key"], token=settings.blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN"))
                session.delete(old)
            session.commit()
        return {"status": "saved", "day": day, "snapshot_sha256": snapshot["sha256"]}
    except Exception as exc:
        with SessionLocal() as session:
            notify(session, "backup-failure:" + day, "incident", "Daily backup failed", type(exc).__name__)
            current = state(session, lock=True)
            current.enabled = current.qualification_enabled = False
            current.pause_reason = "Backup verification failed"
            session.commit()
        return {"status": "failed", "error_type": type(exc).__name__}
    finally:
        release_lease("database_backup", ticket)
