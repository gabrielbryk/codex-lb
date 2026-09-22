## Why

On 2026-09-22, shortly after GPT-6 Sol launched, upstream began rejecting
`gpt-6-sol` on two team-plan accounts with HTTP 404 `model_not_found`
(`The model `gpt-6-sol` does not exist or you do not have access to it.`)
while those accounts' own `/codex/models` lists still advertised the model and
pro-plan accounts served it normally. codex-lb recognizes only the 400
"not supported when using Codex with a ChatGPT account" envelope as an
account/model rejection, so this 404 was surfaced straight to the client and
recorded as a transient account error. Roughly half of `gpt-6-sol` requests
failed even though two accounts could serve them.

## What Changes

- Recognize the `model_not_found` envelope whose message names the requested
  model as a second account/model rejection shape.
- Reuse the existing one-shot pre-acceptance failover for it: exclude the
  rejecting account for that request and retry once on another account that
  advertises the model.
- Keep it out of account health, like the existing entitlement rejection.
- When no replacement is available, surface the original 404 unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: pre-acceptance account/model failover also covers
  the 404 `model_not_found` envelope.
- `account-routing`: that envelope is account-health neutral.

## Impact

- Failure classification predicates in `app/modules/proxy/helpers.py` and the
  stream health write in `app/modules/proxy/_service/streaming/helpers.py`.
- No schema, migration, setting, or public API shape changes. No new
  selection-time memory: an account that keeps rejecting the model is still
  tried first when sticky routing prefers it, then the request fails over.
