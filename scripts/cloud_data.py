"""Safe, repeatable import/restore. Connection strings come from environment only.

uv run python scripts/cloud_data.py import-sqlite --source data/forecastlab.db
uv run python scripts/cloud_data.py restore --snapshot /private/path/snapshot.json.gz
Set FORECASTLAB_IMPORT_URL (direct Postgres), never the pooled app URL.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from forecastlab_api.backup import restore_database, snapshot_database
from forecastlab_api.migrate import apply_migrations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["import-sqlite", "restore"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    target_url = os.environ["FORECASTLAB_IMPORT_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
    if not target_url.startswith("postgresql+psycopg://"):
        parser.error("The target must be a direct Postgres connection")
    target = create_engine(target_url, poolclass=NullPool)
    apply_migrations(target_url)
    if args.command == "import-sqlite":
        if not args.source or not args.source.is_file():
            parser.error("An existing SQLite source is required")
        # Upgrade an online backup, never the user's working database.
        with tempfile.TemporaryDirectory(prefix="forecastlab-import-") as temp:
            path = Path(temp) / "copy.db"
            with sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True) as source, sqlite3.connect(path) as destination:
                source.backup(destination)
            source_url = "sqlite:///" + str(path)
            apply_migrations(source_url)
            source_engine = create_engine(source_url)
            snapshot = snapshot_database(source_engine)
            source_engine.dispose()
    else:
        snapshot = json.loads(gzip.decompress(args.snapshot.read_bytes()))
    receipt = restore_database(target, snapshot)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2))
    args.receipt.chmod(0o600)
    print(json.dumps({"verified": True, "tables": len(receipt["tables"]), "records": sum(receipt["tables"].values()), "receipt": str(args.receipt)}))
    target.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Database exception strings can contain passwords in connection URLs.
        print(json.dumps({"verified": False, "error_type": type(exc).__name__,
            "reason": str(exc) if isinstance(exc, ValueError) else "See local database diagnostics; connection details are redacted"}))
        raise SystemExit(1) from None
