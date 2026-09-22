## Why

The September SCIM and subscription-overflow retirement migrations were merged with the same timestamp and the same parent, leaving Alembic with two heads and causing migration-dependent CI jobs to fail. Their IDs and operations are already merged, so the repair must join their histories without rewriting either migration.

## What Changes

- Add a no-op Alembic merge revision joining the SCIM and subscription-overflow retirement revisions.
- Allow the migration topology guard to accept that same-timestamp pair only after an explicit merge revision joins both branches; unresolved timestamp forks and chained collisions remain errors.
- Verify upgrades from either branch converge on the merge revision and retain the expected schema.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: specify how the topology guard handles already-merged parallel revisions that share a timestamp slot.

## Impact

- Alembic migration graph and topology validation in `app/db/alembic/versions/` and `scripts/check_migration_topology.py`.
- Focused migration tests and the `database-migrations` OpenSpec capability.
- No API, runtime configuration, or data transformation changes.
