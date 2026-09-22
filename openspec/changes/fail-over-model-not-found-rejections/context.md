# Context: fail over model-not-found rejections

## Observed failure

Upstream sometimes denies a model to one account while that account's
`/codex/models` response still lists it. Direct probes on 2026-09-22 with the
proxy's own credentials showed, for `gpt-6-sol`:

| Account plan | listed in `/codex/models` | `gpt-6-sol` | `gpt-6-luna` |
|---|---|---|---|
| pro | yes | 200 | 200 |
| team | yes | 404 `model_not_found` (1 of ~25 attempts succeeded) | 200 |

The 404 body:

```json
{"error": {"message": "The model `gpt-6-sol` does not exist or you do not have access to it.",
           "type": "invalid_request_error", "param": null, "code": "model_not_found"}}
```

## Decisions

- **Same regime as the 400 entitlement rejection.** Both say "this account may
  not use this model"; they differ only in envelope. Reusing the existing
  `account_model_unsupported` path keeps one set of replay-safety rules
  (pre-acceptance only, one replay, no continuation/file-bound migration).
- **Model name must match.** The quoted model must equal the requested model,
  so an unrelated `model_not_found` never triggers failover.
- **A typo'd model also fails over once.** A model no account can serve costs
  one extra attempt, then the client sees the original 404. That is bounded
  and cannot penalize account health.
- **Status gate.** The health-neutral predicate accepts the 404 envelope only
  with status 404 or unknown status (the stream health write receives no
  status on HTTP paths); a known 400 carrying that message keeps its penalty.
- **No selection-time memory.** Remembering per-account model denials would
  avoid the first wasted attempt, but upstream access was observed to flap,
  and a negative cache needs expiry and multi-replica rules. Out of scope.
