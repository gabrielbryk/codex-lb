"""One-time, fail-closed SQLite snapshot to PostgreSQL transfer.

Run against a SQLite backup, never the live writer's database. The target must
already have been migrated to the same Alembic head and contain no rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, Date, DateTime, MetaData, Time, create_engine, func, inspect, select, text
from sqlalchemy.engine import Connection

from app.core.crypto import TokenEncryptor
from app.db.migrate import _script_location
from app.db.migration_url import to_sync_database_url
from app.db.models import Base

BATCH_SIZE = 500
# These rows are inserted by the current Alembic graph in a fresh target.
# The source snapshot is authoritative, so replace them transactionally.
MIGRATION_SEED_COUNTS = {
    "account_usage_rollup_state": 1,
    "cache_invalidation": 2,
    "dashboard_roles": 5,
    "runtime_sentinels": 1,
    "dashboard_settings": 1,
    "dashboard_auth_providers": 3,
}


def _source_tables(source: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }


def _revision(source: sqlite3.Connection, target: Connection) -> str:
    config = Config()
    config.set_main_option("script_location", _script_location())
    heads = ScriptDirectory.from_config(config).get_heads()
    source_heads = [row[0] for row in source.execute("SELECT version_num FROM alembic_version")]
    target_heads = [row[0] for row in target.execute(text("SELECT version_num FROM alembic_version"))]
    if len(heads) != 1 or source_heads != list(heads) or target_heads != list(heads):
        raise ValueError(f"Alembic head mismatch: build={heads}, source={source_heads}, target={target_heads}")
    return heads[0]


def _convert(value: Any, column: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, Boolean):
        if value not in (0, 1):
            raise ValueError(f"invalid Boolean value in {column}")
        return bool(value)
    if isinstance(column.type, DateTime):
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if column.type.timezone and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    if isinstance(column.type, Date):
        return date.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(column.type, Time):
        return time.fromisoformat(value) if isinstance(value, str) else value
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, (bytes, memoryview)):
        return {"bytes": bytes(value).hex()}
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _digest_rows(rows: Any) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps([_canonical(value) for value in row], ensure_ascii=False, separators=(",", ":")).encode()
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _table_layout(source: sqlite3.Connection, target: Connection, metadata: MetaData) -> list[Any]:
    expected = set(Base.metadata.tables)
    source_tables = _source_tables(source)
    target_tables = set(inspect(target).get_table_names())
    required = expected | {"alembic_version"}
    if source_tables != required or target_tables != required:
        raise ValueError(
            f"table mismatch: source_extra={sorted(source_tables - required)}, "
            f"source_missing={sorted(required - source_tables)}, "
            f"target_extra={sorted(target_tables - required)}, "
            f"target_missing={sorted(required - target_tables)}"
        )
    metadata.reflect(bind=target, only=sorted(expected))
    for name in sorted(expected):
        source_columns = {row[1] for row in source.execute(f'PRAGMA table_info("{name}")')}
        target_columns = set(metadata.tables[name].columns.keys())
        if source_columns != target_columns:
            raise ValueError(
                f"column mismatch for {name}: source={sorted(source_columns)}, target={sorted(target_columns)}"
            )
    return [metadata.tables[table.name] for table in Base.metadata.sorted_tables]


def _counts(source: sqlite3.Connection, target: Connection, tables: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        name = table.name
        source_count = source.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        target_count = target.execute(select(func.count()).select_from(table)).scalar_one()
        counts[name] = source_count
        if target_count != source_count:
            raise ValueError(f"count mismatch {name}: source={source_count}, target={target_count}")
    return counts


def _verify(source: sqlite3.Connection, target: Connection, tables: list[Any], key_file: Path) -> dict[str, str]:
    if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("source foreign key check failed")
    digests: dict[str, str] = {}
    for table in tables:
        columns = list(table.columns)
        order = [column.name for column in table.primary_key.columns]
        if not order:
            raise ValueError(f"table {table.name} has no primary key")
        quoted = ", ".join(f'"{column.name}"' for column in columns)
        ordering = ", ".join(f'"{name}"' for name in order)
        cursor = source.execute(f'SELECT {quoted} FROM "{table.name}" ORDER BY {ordering}')
        source_rows = ([_convert(value, column) for value, column in zip(row, columns, strict=True)] for row in cursor)
        source_digest = _digest_rows(source_rows)
        target_rows = target.execute(select(*columns).order_by(*(table.c[name] for name in order)))
        target_digest = _digest_rows(target_rows)
        if source_digest != target_digest:
            source_again = source.execute(f'SELECT {quoted} FROM "{table.name}" ORDER BY {ordering}')
            target_again = target.execute(select(*columns).order_by(*(table.c[name] for name in order)))
            for index, (left, right) in enumerate(zip(source_again, target_again, strict=True)):
                for column, source_value, target_value in zip(columns, left, right, strict=True):
                    if _canonical(_convert(source_value, column)) != _canonical(target_value):
                        raise ValueError(
                            f"value mismatch in {table.name} row={index} column={column.name} "
                            f"source_type={type(source_value).__name__} target_type={type(target_value).__name__}"
                        )
            raise ValueError(f"value digest mismatch in {table.name}")
        digests[table.name] = target_digest
    # PostgreSQL enforces foreign keys on every INSERT. Verify all imported
    # encrypted account tokens with application crypto and the existing key.
    if not key_file.is_file():
        raise ValueError("existing encryption key file is required")
    encryptor = TokenEncryptor(key_file=key_file)
    accounts = target.execute(
        text("SELECT access_token_encrypted, refresh_token_encrypted, id_token_encrypted FROM accounts")
    )
    for row in accounts:
        for encrypted in row:
            encryptor.decrypt(bytes(encrypted))
    return digests


def transfer(source_path: Path, target_url: str, key_file: Path, *, dry_run: bool) -> dict[str, Any]:
    if not source_path.is_file():
        raise ValueError("SQLite snapshot does not exist")
    if not key_file.is_file():
        raise ValueError("existing encryption key file is required")
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    engine = create_engine(to_sync_database_url(target_url))
    try:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite snapshot failed quick_check")
        with engine.begin() as target:
            if target.dialect.name != "postgresql":
                raise ValueError("target must be PostgreSQL")
            # A transaction-scoped advisory lock prevents two importers from
            # passing the empty-target check concurrently.
            target.execute(text("SELECT pg_advisory_xact_lock(739258124)"))
            revision = _revision(source, target)
            tables = _table_layout(source, target, MetaData())
            populated = {
                table.name: target.execute(select(func.count()).select_from(table)).scalar_one() for table in tables
            }
            blocked = {
                name: count for name, count in populated.items() if count and MIGRATION_SEED_COUNTS.get(name) != count
            }
            if blocked:
                raise ValueError(f"target contains application rows: {blocked}")
            source_counts = {
                table.name: source.execute(f'SELECT count(*) FROM "{table.name}"').fetchone()[0] for table in tables
            }
            seeds = {name: count for name, count in populated.items() if count}
            report: dict[str, Any] = {
                "revision": revision,
                "source": str(source_path),
                "tables": len(tables),
                "source_counts": source_counts,
                "migration_seeds_to_replace": seeds,
                "target_empty_except_migration_seeds": True,
                "dry_run": dry_run,
            }
            if dry_run:
                return report
            for table in reversed(tables):
                if table.name in seeds:
                    target.execute(table.delete())
            for table in tables:
                columns = list(table.columns)
                names = [column.name for column in columns]
                quoted = ", ".join(f'"{name}"' for name in names)
                cursor = source.execute(f'SELECT {quoted} FROM "{table.name}"')
                while rows := cursor.fetchmany(BATCH_SIZE):
                    payload = [
                        dict(
                            zip(
                                names,
                                (_convert(value, column) for value, column in zip(row, columns, strict=True)),
                                strict=True,
                            )
                        )
                        for row in rows
                    ]
                    target.execute(table.insert(), payload)
            _counts(source, target, tables)
            digests = _verify(source, target, tables, key_file)
            sequences: dict[str, int] = {}
            for table in tables:
                for column in table.primary_key.columns:
                    sequence = target.execute(
                        text("SELECT pg_get_serial_sequence(:table, :column)"),
                        {"table": table.name, "column": column.name},
                    ).scalar_one()
                    if sequence:
                        maximum = target.execute(select(func.max(column))).scalar_one()
                        next_value = (maximum or 0) + 1
                        # setval is nontransactional, so do this only after
                        # all transactional data verification has passed.
                        target.execute(
                            text("SELECT setval(CAST(:sequence AS regclass), :value, false)"),
                            {"sequence": sequence, "value": next_value},
                        )
                        sequences[f"{table.name}.{column.name}"] = next_value
            report.update({"digests": digests, "next_sequence_values": sequences, "verified": True})
            return report
    finally:
        source.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="consistent SQLite snapshot")
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(transfer(args.source, args.target_url, args.key_file, dry_run=args.dry_run), sort_keys=True))


if __name__ == "__main__":
    main()
