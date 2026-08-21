#!/usr/bin/env python3
# Copyright 2026 Carlos Henrique <carloshenriquedeoliveiragomes@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-off helper to copy PyHSS data from a SQLite snapshot into an already
schema-provisioned PostgreSQL database.

Usage:
    PYHSS_CONFIG=/path/to/postgres_config.yaml PYTHONPATH=lib \
        python3 tools/migrate_sqlite_to_postgres.py /path/to/hss_snapshot.db

The destination database must already have the PyHSS schema created (run any
PyHSS service with main_service=True against the target database once, or
just instantiate database.Database(..., main_service=True)).
"""
import sys
import sqlite3

import sqlalchemy
from sqlalchemy.exc import IntegrityError

from database import Base, Database
from logtool import LogTool
from pyhss_config import config


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <sqlite_snapshot_path>")
        sys.exit(1)
    sqlite_path = sys.argv[1]

    if str(config['database']['db_type']).lower() != 'postgresql':
        print("PYHSS_CONFIG must point at a postgresql database.database config")
        sys.exit(1)

    db = Database(LogTool(config), main_service=False)
    engine = db.engine

    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    tables = [t for t in Base.metadata.sorted_tables if t.name != 'database_schema_version']

    with engine.begin() as conn:
        for table in tables:
            columns = [c.name for c in table.columns]
            placeholders = ",".join(columns)
            try:
                rows = src.execute(f"SELECT {placeholders} FROM {table.name}").fetchall()
            except sqlite3.OperationalError as e:
                print(f"[skip] {table.name}: {e}")
                continue

            if not rows:
                print(f"[----] {table.name}: 0 rows")
                continue

            payload = [dict(row) for row in rows]
            inserted = 0
            skipped = []
            for record in payload:
                try:
                    with conn.begin_nested():
                        conn.execute(table.insert(), [record])
                    inserted += 1
                except IntegrityError as e:
                    skipped.append((record, str(e.orig).strip()))

            print(f"[done] {table.name}: {inserted} rows"
                  + (f", {len(skipped)} skipped (FK violation)" if skipped else ""))
            for record, reason in skipped:
                print(f"       skipped {table.name} pk={ {c: record[c] for c in [p.name for p in table.primary_key.columns]} }: {reason}")

            pk_columns = [c.name for c in table.primary_key.columns]
            if len(pk_columns) == 1:
                pk = pk_columns[0]
                seq_expr = sqlalchemy.text(
                    "SELECT setval(pg_get_serial_sequence(:table, :pk), "
                    "COALESCE((SELECT MAX(" + pk + ") FROM " + table.name + "), 1), true)"
                )
                seq_name = conn.execute(
                    sqlalchemy.text("SELECT pg_get_serial_sequence(:table, :pk)"),
                    {"table": table.name, "pk": pk},
                ).scalar()
                if seq_name:
                    conn.execute(seq_expr, {"table": table.name, "pk": pk})
                    print(f"       sequence {seq_name} synced")

    src.close()
    print("Migration complete.")


if __name__ == '__main__':
    main()
