"""Exercise the September SCIM and overflow-retirement merge from either branch."""

from __future__ import annotations

from pathlib import Path

import pytest
from anyio import to_thread
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration

_SCIM = "20260914_000000_add_scim_tokens"
_OVERFLOW_RETIREMENT = "20260914_000000_drop_subscription_overflow_schema"
_MERGE = "20260922_193800_merge_scim_and_overflow_retirement"


@pytest.mark.asyncio
@pytest.mark.parametrize("starting_branch", [_SCIM, _OVERFLOW_RETIREMENT])
async def test_september_migration_branches_converge(starting_branch: str, tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / f'{starting_branch}.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, starting_branch, bootstrap_legacy=False))

    result = await to_thread.run_sync(lambda: run_upgrade(db_url, _MERGE, bootstrap_legacy=False))

    assert result.current_revision == _MERGE
    assert await to_thread.run_sync(lambda: check_schema_drift(db_url)) == ()

    engine = create_engine(db_url.replace("+aiosqlite", ""))
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert tuple(connection.execute(text("SELECT version_num FROM alembic_version")).scalars()) == (_MERGE,)
            assert inspector.has_table("dashboard_scim_tokens")
            assert "user_name" in {column["name"] for column in inspector.get_columns("dashboard_identities")}
            assert not inspector.has_table("model_source_pins")
            settings_columns = {column["name"] for column in inspector.get_columns("dashboard_settings")}
            assert "subscription_overflow_source_id" not in settings_columns
            assert "subscription_overflow_drain_until" not in settings_columns
    finally:
        engine.dispose()
