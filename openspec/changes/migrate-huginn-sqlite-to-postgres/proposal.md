## Why

The Huginn codex-lb service still uses a live SQLite database. Moving it to upstream-supported PostgreSQL is an independent prerequisite for overlapping application generations; the existing database contents and encryption key must survive the one-time maintenance cutover.

## What Changes

- Add a bounded, repeatable SQLite-to-PostgreSQL transfer tool with dry-run, exact schema checks, row and value verification, and sequence repair.
- Rehearse against an isolated PostgreSQL service using a consistent SQLite snapshot, then import a final quiesced snapshot during maintenance.
- Change the Huginn codex-lb service's canonical `CODEX_LB_DATABASE_URL` to PostgreSQL and verify real authenticated routes and stored credentials after restart.
- Preserve the final SQLite snapshot as archival evidence; PostgreSQL becomes authoritative after accepting a write.

## Capabilities

### New Capabilities

- `database-backend-transfer`: Defines safe one-time transfer and verification between supported database backends.

### Modified Capabilities

None. PostgreSQL runtime behavior is already supported upstream.

## Impact

- codex-lb: transfer command and focused tests; no inference API or schema change.
- selfhosted: isolated PostgreSQL service, backup, service environment, and maintenance runbook.
- Codex clients: one planned interruption during the database cutover; later blue/green work is separate.
