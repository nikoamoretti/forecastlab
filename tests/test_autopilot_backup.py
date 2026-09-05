from __future__ import annotations

import copy

import pytest
from sqlalchemy import create_engine, text

from forecastlab_api.backup import restore_database, snapshot_database, verify_snapshot
from forecastlab_api.migrate import apply_migrations


def test_snapshot_restore_parity_idempotence_and_corruption(client, tmp_path):
    from forecastlab_api.db import engine
    snapshot = snapshot_database(engine)
    url = 'sqlite:///' + str(tmp_path / 'restored.db')
    apply_migrations(url)
    target = create_engine(url)
    receipt = restore_database(target, snapshot)
    assert receipt['verified'] and 'contract_hashes' in receipt['checks']
    assert restore_database(target, snapshot)['snapshot_sha256'] == receipt['snapshot_sha256']
    corrupted = copy.deepcopy(snapshot)
    corrupted['tables']['questions'][0]['original_text'] = 'Changed after snapshot'
    with pytest.raises(ValueError, match='backup_integrity_failed'):
        verify_snapshot(corrupted)
    with target.begin() as connection:
        connection.execute(text("UPDATE questions SET original_text='Different target record'"))
    with pytest.raises(ValueError, match='restore_existing_record_differs'):
        restore_database(target, snapshot)
    target.dispose()


def test_append_only_records_cannot_be_updated_by_raw_sql(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'protected.db')
    apply_migrations(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO autopilot_policies (id,revision,config_json,fingerprint,approved_by,created_at) VALUES ('p',1,'{}','hash','owner','2026-09-04')"))
    for sql in ("UPDATE autopilot_policies SET revision=2", "DELETE FROM autopilot_policies"):
        with engine.begin() as connection, pytest.raises(Exception, match='append.only'):
            connection.execute(text(sql))
    engine.dispose()
