## ADDED Requirements

### Requirement: Direct upstream WebSocket failures degrade to HTTP without replay
When a direct upstream Responses WebSocket has at least one pending `response.create` that crossed the send boundary and has not received `response.completed`, `response.failed`, `response.incomplete`, or `error`, a socket close, socket error, or ambiguous send failure MUST terminally fail the affected request without automatic replay and MUST arm the existing 60-second per-instance upstream WebSocket transport-failure marker. The same behavior MUST apply to native WebSocket ingress and the HTTP Responses bridge.

The marker MUST NOT be armed for an account-specific routed proxy endpoint, process-wide network loss, downstream client disconnect, local cancellation, request-budget expiry, replay-sequence refusal, internal reader crash, an idle socket with no sent pending request, a request already completed by a terminal event, or an in-band `server_error` or `server_is_overloaded`. A qualifying transport failure MUST remain account-neutral and MUST NOT penalize the selected account.

While the marker is active, subsequent Responses WebSocket handshakes MUST receive HTTP 426 and subsequent HTTP requests in automatic transport mode MUST use upstream HTTP. Existing TTL expiry and a successful direct-WebSocket probe MUST clear the degraded state. File pins, response owners, turn-state owners, and API-key reservation settlement MUST remain unchanged. The service MUST emit a credential-free low-cardinality log with trigger `receive_close`, `receive_error`, or `send_error` and MUST reuse `codex_lb_upstream_transport_decisions_total{policy="recent_ws_failure"}` for the fallback decision.

#### Scenario: Direct receive close degrades after dispatch
- **WHEN** a direct upstream socket closes after a pending request crossed the send boundary and before a terminal event
- **THEN** that request fails once without replay, no account penalty is written, and the marker is armed with trigger `receive_close`

#### Scenario: Direct receive error degrades after dispatch
- **WHEN** a direct upstream socket errors after a pending request crossed the send boundary and before a terminal event
- **THEN** that request fails once without replay, no account penalty is written, and the marker is armed with trigger `receive_error`

#### Scenario: Ambiguous direct send failure degrades
- **WHEN** a direct upstream send fails after delivery becomes ambiguous
- **THEN** the request fails once without replay and the marker is armed with trigger `send_error`

#### Scenario: Routed and unrelated failures do not degrade globally
- **WHEN** the failure is routed, process-wide, downstream-caused, locally cancelled, budget-expired, replay-refused, an internal reader crash, idle, already terminal, or in-band
- **THEN** the global marker remains unchanged and existing settlement behavior applies

#### Scenario: Codex falls back and later recovers
- **WHEN** a qualifying failure arms the marker
- **THEN** the next WebSocket handshake receives 426, the HTTP retry retains its required account and uses upstream HTTP, and direct WebSocket is admitted again after expiry or a successful direct probe
