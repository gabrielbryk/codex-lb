# Fork patch manifest

The `gabe/fork` branch is a downstream patch stack on `main` at upstream
commit `d6a7ca66` (soju06/codex-lb). This file is the human review surface
for what the fork carries beyond upstream and why.

## Maintained logical patches

| Patch | SHA | Category | Upstream status |
|---|---|---|---|
| `native-giveup-retryable-error` | `862d5cb3` | defensive | Not yet upstreamed. When codex-lb exhausts its own retries/replays for an upstream capacity/overload error on behalf of a native Codex client, it used to re-raise after the 200 had already been sent, closing the SSE/WS stream with zero bytes ("Stream disconnected before completion"). This patch emits a terminal `response.failed` with `error.code = "rate_limit_exceeded"` and a "try again in `<N>`s" message instead, so codex-rs's own reconnect logic (which treats `server_is_overloaded`/`slow_down` as terminal and only retries + parses a delay from `rate_limit_exceeded`) engages instead of the client retrying blind. Touches `app/core/errors.py`, `app/modules/proxy/api.py` (both native give-up gates in `_stream_response_error_events` / `_normalize_public_responses_stream`), `app/modules/proxy/_service/http_bridge/request_submit.py` (preserves the upstream cause on the denied-anchor fail-closed raise), and `app/modules/proxy/_service/websocket/helpers.py` (`_sanitize_websocket_terminal_error_fields` relabels `server_is_overloaded`/`slow_down` terminal frames the same way). Non-native/OpenAI-compatible clients are unaffected. Spec updated: `openspec/specs/responses-api-compat/spec.md` ("Native Codex preserves upstream failure lifecycle"). Side effect: denied-anchor failures in `http_bridge/request_submit.py` now record `upstream_error_code=previous_response_not_found` (previously `stream_incomplete`) in RequestLog telemetry. |
