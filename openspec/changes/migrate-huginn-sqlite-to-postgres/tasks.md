## 1. Transfer Tool

- [ ] 1.1 [codex-lb] Implement dry-run and exact-head/empty-target checks; verify mismatch and populated-target fixtures fail without writes.
- [ ] 1.2 [codex-lb] Implement bounded table copy, value conversion, and sequence reset; verify a synthetic store with booleans, timestamps, encrypted bytes, foreign keys, and generated IDs round-trips to PostgreSQL.
- [ ] 1.3 [codex-lb] Implement full table-count and foreign-key verification plus application credential-decryption smoke; verify corrupt or missing data fails the report.

## 2. PostgreSQL Substrate

- [ ] 2.1 [selfhosted] Provision an isolated PostgreSQL service with persistent storage, restricted credentials, network reachability from codex-lb, health checks, and a restore-tested backup; verify no other service owns its volume or database.
- [x] 2.2 [selfhosted] Snapshot live SQLite with the backup API, rehearse transfer into a disposable database, and record duration, counts, schema head, destination size, and verification result.

## 3. Maintenance Cutover

- [x] 3.1 [selfhosted] Identify active clients and in-flight work, quiesce all SQLite writers at a quiet point, and take a final source snapshot; verify no writer can change SQLite after the snapshot.
- [x] 3.2 [selfhosted] Import into an empty migrated PostgreSQL target and require all transfer verification gates to pass before changing the Coolify service configuration.
- [x] 3.3 [selfhosted] Set `CODEX_LB_DATABASE_URL` on the existing managed service, retain its image and encryption key, restart through Coolify, and verify PostgreSQL-backed readiness, account inventory, dashboard auth, API-key routing, and authenticated inference.
- [ ] 3.4 [selfhosted] Verify PostgreSQL backup/restore and document the first PostgreSQL application write as the irreversible SQLite rollback boundary; preserve the frozen source snapshot.

## Status (2026-09-22)

Cut over on 2026-09-22 20:51 UTC. The PostgreSQL service is provisioned and verified by
the transfer report, but has no scheduled backup or restore test yet (2.1, 3.4).
The transfer tool was verified by the live rehearsal and final import only; the
fixture-based failure tests in 1.x were skipped by request.
