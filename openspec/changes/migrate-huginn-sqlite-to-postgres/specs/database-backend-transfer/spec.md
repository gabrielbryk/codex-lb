## Purpose

Define safe, verifiable transfer of an existing codex-lb store from SQLite to PostgreSQL.

## ADDED Requirements

### Requirement: Transfer requires a matching supported schema and isolated destination

The transfer tool MUST require one known Alembic head shared by the source SQLite database, target PostgreSQL database, and executing codex-lb build. It MUST reject a target containing application data and MUST NOT mutate the source. A dry run MUST report the source schema, table inventory, destination state, and blocking conditions without copying data.

#### Scenario: Mismatched schema fails before import

- **WHEN** the source, destination, or tool build has a different Alembic head
- **THEN** transfer fails without inserting application rows

#### Scenario: Populated destination fails before import

- **WHEN** the destination contains application data
- **THEN** transfer fails without changing either database

### Requirement: Transfer preserves durable application values

The transfer MUST copy all application tables in bounded batches while preserving primary keys, foreign-key relationships, encrypted bytes, booleans, enums, timestamps, and nullable values. It MUST reset PostgreSQL sequences used by generated IDs to values above imported IDs. It MUST fail when a source table lacks a matching destination or model table.

#### Scenario: Existing data remains usable

- **WHEN** a transfer completes
- **THEN** all table counts and foreign-key checks match the source snapshot
- **AND** application code can decrypt representative stored credentials with the unchanged encryption key
- **AND** inserting a new generated-ID row cannot collide with an imported ID

### Requirement: Cutover requires explicit writer quiescence and verification

The production runbook MUST quiesce SQLite writers before its final snapshot, verify transfer results before changing the service database URL, and check authenticated application routes after restart. Once PostgreSQL accepts an application write, the SQLite snapshot MUST be treated as archival and MUST NOT be used as a live rollback target.

#### Scenario: Final cutover succeeds

- **WHEN** the operator switches codex-lb to the verified PostgreSQL database
- **THEN** readiness, account inventory, dashboard authentication, API-key routing, and authenticated inference succeed against PostgreSQL

#### Scenario: Failure after a PostgreSQL write

- **WHEN** the service has accepted a write in PostgreSQL and must roll back application code
- **THEN** rollback keeps PostgreSQL authoritative and does not restore the stale SQLite file
