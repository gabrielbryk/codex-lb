## 1. Classification

- [x] 1.1 Match the `model_not_found` envelope with message
  `The model `<model>` does not exist or you do not have access to it.` for the
  requested model in `_is_account_model_unsupported_error`.
- [x] 1.2 Treat that envelope as model-scoped (health neutral) only when the
  status is 404 or unknown.
- [x] 1.3 Unit-cover matching and non-matching code, message, model, and status.

## 2. Product path

- [x] 2.1 Cover the routed raw HTTP/SSE Responses path: a 404 `model_not_found`
  on the first account retries once on a second advertising account without an
  account-health penalty.
- [x] 2.2 Cover the no-replacement path: the original 404 reaches the client.

## 3. Verification

- [x] 3.1 Run focused proxy-utils and transient-retry tests.
- [x] 3.2 Run changed-file Ruff and formatting checks.
- [x] 3.3 Run strict OpenSpec validation for this change.
