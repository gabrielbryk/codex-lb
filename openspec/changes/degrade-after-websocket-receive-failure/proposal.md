## Why

A direct upstream Responses WebSocket can fail after `response.create` crosses the send boundary but before any terminal response arrives. The current transport-degradation marker covers connect failures, so Codex reconnects to WebSocket and can repeat the same failure instead of taking its built-in HTTP fallback.

## What Changes

- Arm the existing 60-second per-instance WebSocket transport-failure marker for qualifying direct receive-close, receive-error, and ambiguous send failures.
- Terminally fail the ambiguous in-flight request without replay or account penalty.
- Apply the same rule to native WebSocket ingress and the HTTP bridge reader.
- Preserve route, network-loss, downstream-disconnect, cancellation, timeout, replay-refusal, in-band error, ownership, and settlement boundaries.
- Reuse the existing recent-failure transport decision metric and add one low-cardinality trigger log.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Extend direct WebSocket transport degradation from connect failures to qualifying post-send transport failures.

## Impact

Native Responses WebSocket settlement, HTTP bridge upstream-reader settlement, transport-health logging/tests, and the existing HTTP fallback decision path are affected. There is no setting or database migration.
