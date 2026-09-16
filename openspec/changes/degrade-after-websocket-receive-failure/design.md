## Context

The service already owns a 60-second process-local marker used by WebSocket handshake denial and HTTP upstream-transport selection. Native WebSocket and bridge request states both record the send boundary in `response_create_sent_at` and route provenance in `upstream_proxy_route_mode`. Their existing terminal settlement paths already release reservations and can suppress account penalties.

## Goals / Non-Goals

**Goals:** reuse those existing primitives, arm only on unambiguous direct-transport evidence, and keep settlement single-owner.

**Non-Goals:** request replay, cross-account recovery, new configuration, durable marker state, or changes to continuity ownership.

## Decisions

### Centralize qualification and logging

A support helper accepts route mode, sent-pending state, transport kind, and error code. It rejects non-direct routes, empty pending sets, non-terminal transport kinds, and process-network-loss, then arms the existing marker and logs only the trigger. Callers retain ownership of request settlement.

### Native receive failures use terminal settlement

The native transport-end path qualifies before replay selection. A qualifying direct failure bypasses pre-created replay, fails reader-owned sent requests through the existing finalizer with `penalize_account=False`, and closes the transport.

### Bridge receive failures arm before existing retirement

The bridge reader snapshots sent pending state and session route mode before its existing failure/retirement path. Qualifying close/error frames arm the marker; the current bridge settlement remains authoritative for request failure, reservation release, and durable ownership cleanup.

### Ambiguous send failures qualify at the send catch

The native and bridge send catch paths mark only after send has been attempted and only for a direct route. They retain their current terminal failure and cleanup paths and do not replay the ambiguous request.

## Risks / Trade-offs

- [Over-broad degradation] -> Require direct route provenance, sent pending work, terminal transport evidence, and exclude process-wide loss.
- [Double settlement] -> The helper only marks/logs; existing reader or sender ownership performs finalization.
- [Duplicate execution] -> Qualifying native receive failure bypasses replay, and ambiguous send paths remain terminal.

## Migration Plan

Merge into the fork's main branch, run all transport/bridge/ownership/settlement gates, deploy only at zero connected clients, switch the runtime setting to `auto`, and exercise a forced direct premature close. If the canary is inconclusive or fails, restore `http` without rolling back ownership state.
