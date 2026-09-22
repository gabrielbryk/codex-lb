## Context

Upstream codex-lb already supports PostgreSQL through SQLAlchemy, `asyncpg`, Alembic, repository branches, migration locking, and multi-replica coordination. Huginn's live Coolify service currently mounts `/data/codex-lb`, uses its default SQLite URL, and has one application container. A 2026-09-22 source inspection found 69 SQLite tables including `alembic_version`, with every application table matching the current SQLAlchemy metadata. The live file occupies 3.30 GB, about 512 MiB of which are live pages.

## Goals / Non-Goals

**Goals:** Preserve every durable row and key, prove the destination can serve the same application, make the maintenance boundary explicit, and enable later overlapping generations.

**Non-Goals:** Zero-downtime database conversion, schema change, dual writing, blue/green routing, or SQLite database replication.

## Decisions

### Transfer from a frozen SQLite snapshot into an empty migrated PostgreSQL schema

Rehearsals use SQLite's online backup API to obtain a consistent source snapshot. The final import uses a snapshot after codex-lb and other SQLite writers are quiesced. PostgreSQL is migrated to the exact source Alembic head first, then the transfer copies application tables in dependency order in bounded batches. It preserves IDs and encrypted byte strings; converts SQLite booleans and datetime text to PostgreSQL-compatible values; and resets sequences after explicit ID insertion. The destination must be empty apart from migration-created schema and seed rows that the transfer intentionally replaces. No script points production traffic at the rehearsal database.

The source and destination must report the same single Alembic revision. Unknown or multiple heads fail before import. A completed import produces a machine-readable report of table counts, sequence state, foreign-key validity, selected row digests, and application-level credential decryption. The final report is tied to the source snapshot's identity and destination database identity.

### Establish a durable cutover boundary

The maintenance procedure captures the current image and configuration, checks active clients and in-flight requests, stops all SQLite writers, takes a final snapshot, and imports into an empty PostgreSQL database. After verification, it sets only `CODEX_LB_DATABASE_URL` on the existing Coolify service and restarts it through the managed path. Readiness, dashboard access, API-key routing, account inventory, and a real authenticated inference request are checked against the new database. The encryption key remains the existing `/data/codex-lb/encryption.key`.

Before PostgreSQL accepts an application write, rollback can restore the prior service configuration against the frozen SQLite file. After the first PostgreSQL write, the SQLite snapshot is stale and is never used as a live rollback target; application rollback retains PostgreSQL.

## Risks / Trade-offs

- The final maintenance window lasts through snapshot, import, and validation; rehearse import time and size first.
- SQLite and PostgreSQL have different Boolean, datetime, enum, and autoincrement representations; the transfer verifies them with application reads and sequence probes.
- Existing sessions may be interrupted by the one required service restart; the cutover waits for a verified quiet point and does not use the deployment script's forced-client override.
- The current source tree can advance independently of the deployed image; pin migration tooling and source schema to the deployed image's Alembic head before the final import.

## Migration Plan

1. Build and test the transfer command; create an isolated PostgreSQL target with backup and restore checks.
2. Make a consistent SQLite rehearsal snapshot while the service continues running, migrate an empty target, transfer, verify, and time the operation.
3. At a quiet point, stop SQLite writers, take a final snapshot, reset the target, transfer, and verify all gates.
4. Update the managed service configuration with the PostgreSQL URL and restart without changing the application image.
5. Check real routes, account identity and decryption, application writes, and backup. Keep the SQLite snapshot frozen.
