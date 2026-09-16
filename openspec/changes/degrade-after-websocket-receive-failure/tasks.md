## 1. Transport Qualification

- [x] 1.1 Add a shared direct post-send transport-failure qualifier/marker with low-cardinality trigger logging and focused unit tests
- [x] 1.2 Prove routed, process-network, idle, completed, downstream, cancellation, timeout, replay-refusal, reader-crash, and in-band failures do not arm the marker

## 2. Native WebSocket

- [x] 2.1 Arm degradation for qualifying receive close/error and bypass automatic replay while preserving terminal settlement and no account penalty
- [x] 2.2 Arm degradation for ambiguous direct send errors while preserving request ownership, reservation release, and no replay

## 3. HTTP Bridge

- [x] 3.1 Apply the same receive close/error rule to the bridge upstream reader without duplicating retirement or continuity logic
- [x] 3.2 Apply the same ambiguous send rule to bridge submission and preserve settlement ordering

## 4. Verification and Rollout

- [ ] 4.1 Run targeted transport, bridge, ownership, settlement, and observability tests
- [ ] 4.2 Run lint, typecheck, unit, bridge, core integration shards, e2e, strict change/spec validation, and Codex review
- [ ] 4.3 Complete the unchecked continuity-owner validation tasks without changing its fail-closed ownership behavior
- [ ] 4.4 Merge to main, deploy only at zero connected clients, and run the controlled auto canary; restore HTTP if evidence is inconclusive
