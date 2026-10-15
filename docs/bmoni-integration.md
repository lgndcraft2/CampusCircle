# B Moni Embedded: integration findings

Sources checked on 2026-10-15:
- https://embedded-docs.bmoni.com/markets/nigeria/ and `llms-full.txt` (the full docs dump)
- The sandbox OpenAPI document at `https://embedded-dev.bmoni.com/docs/openapi.json`
- Live calls to the sandbox using the shared sandbox key published in the docs

## What the provider supports (Nigeria)

| Capability | Documented | Endpoint(s) | Notes |
| --- | --- | --- | --- |
| Sandbox base URL | yes | `https://embedded-dev.bmoni.com` (prod `https://embedded.bmoni.com`) | Origin only, no trailing `/v1` |
| Auth | yes | `x-api-key` header | Shared sandbox key in docs; organisations email developers@bkey.me |
| Create user | yes | `POST /v1/users` → `{user:{bmoniUserId,…}}` | `bmoniUserId` scopes every later call; 409 if email/phone exists |
| Smart wallet | yes | `POST …/smart-wallets/owner-proof-challenges` → sign (EIP-191) → `POST …/create-managed` | Wallet currency `CNGN`; live API reports it as `NGN` |
| KYC (BVN) | yes | `PATCH …/kyc`, document uploads, `POST …/onboarding/start-nigeria` | Requires BVN + ID documents; sandbox matches fixed personas only |
| Virtual NGN account (NUBAN) | yes | issued by `start-nigeria`; `GET …/bank-accounts/deposit-accounts/NGN` | Depends on KYC |
| Balances | yes | `GET …/smart-wallets/account/balances` | Decimal strings |
| Transfers / payouts | yes | proposals → approve → sign hash; `…/offramp/nigeria` | Needs funded wallet; test tokens are credited manually (~1 business day) |
| Webhooks | yes | `POST /v1/webhooks/config` (needs `partnerId`) | HMAC-SHA256 of raw body in `X-Webhook-Signature`; dedupe on `id` |
| Recurring debit mandates | **not documented** | — | So no automatic repayment collection is built |
| Business/organisation wallets | Mexico only | — | Cannot be used as a Nigerian circle treasury |

## What CampusCircle implements

`backend/app/bmoni.py` (adapter) and `backend/app/integrations.py` (routes).

1. **Circle sandbox treasury.** The circle owner calls `POST /api/v1/circles/{id}/treasury`:
   - It creates a B Moni user with synthetic contact details (`treasury+<circleId>@example.com`, a derived `+23490…` number).
   - It provisions a CNGN smart wallet. The owner key is derived per circle with `HMAC(BMONI_SIGNER_SEED, circleId)`, so no key is stored in the database. This is a **sandbox-only custody shortcut**; B Moni recommends a device secure element or a KMS/HSM for real keys.
   - It is resumable: if the user was created but the wallet step failed, a retry continues from the wallet step.
2. **Provider balance read** (`GET …/treasury/balance`, admins only). Read-only and labelled *provider-reported*. Nothing is written to the ledger.
3. **Webhooks** at `POST /api/v1/webhooks/bmoni`:
   - Signature: HMAC over the raw bytes, compared in constant time. Invalid signatures get a 401.
   - Deduplication: a unique `(provider, event_id)` record.
   - `employee.deposit.completed` for a known treasury user becomes a `sandbox_deposit` journal (`bmoni_sandbox_cash` / `bmoni_sandbox_clearing`) with `provenance = bmoni_sandbox` and `status = provider_confirmed`.
   - Unknown users are stored as `unmatched`. Malformed amounts are stored as `rejected`. Other event types are stored as `ignored`.
4. **Outcome model.** Every call returns `ok`, `failed`, or `uncertain`.
   - A timeout, 5xx, or non-JSON response on a POST is `uncertain`: the provider may have acted.
   - Uncertain user creation is reconciled by paging `GET /v1/users` for the synthetic email before giving up.
   - Uncertain wallet creation is reconciled with `GET …/smart-wallets/account/wallets`.
   - GET failures are always `failed`, so they are safe to retry.
5. **Separation.** Simulated loans and contributions use account `cash`. Sandbox deposits use `bmoni_sandbox_cash` and cannot fund simulated loans.

## Verified live (2026-10-15, shared sandbox key)

| Call | Result |
| --- | --- |
| `GET /v1/smart-wallets/supported-currencies` | 200 `["USDB","CNGN","CADC","EURe","GBPe","MEXe"]` |
| `POST /v1/users` | 201, `bmoniUserId` returned |
| owner-proof challenge + `create-managed` (server-side EIP-191 signature) | 201, wallet active with an address |
| `GET …/smart-wallets/account/balances` | 200 `{"smartAccountAddress":…,"balances":[{"currency":"NGN","balance":"0"}]}` |

**Doc discrepancy found:** the OpenAPI schema wraps balances in `data`, but the live sandbox returns them unwrapped. The adapter accepts both, and a unit test covers each shape.

## Not implemented, and why

- **KYC / BVN / NUBAN.** The product brief forbids collecting real BVNs. The sandbox only accepts fixed personas, so a demo would either impersonate a persona or collect identity data.
- **Transfers, payouts, loan disbursement through B Moni.** These need a funded wallet. Test tokens are credited manually on request, and a server-signed transfer from a student-pooled wallet raises custody questions beyond a sandbox demo.
- **Webhook subscription setup.** `POST /v1/webhooks/config` needs a `partnerId` and a public HTTPS callback URL. Neither was available, so **webhook delivery was not observed live**; only the receiver logic is tested, using signed fixtures.
- **Production.** The base URL is pinned to the sandbox at startup (`config.validate_runtime`).

## Configuration

```
BMONI_API_KEY=            # sandbox x-api-key
BMONI_SIGNER_SEED=        # >= 32 random characters
BMONI_WEBHOOK_SECRET=     # secretKey from POST /v1/webhooks/config
```

When these are missing, the treasury panel says so, the API returns 503 with a clear message, and the simulated ledger keeps working.

## Tests

`backend/tests/test_integrations.py` covers:
- create-user success, conflict, timeout, non-JSON, and missing-id responses
- recovering the owner address from the wallet signature
- both balance response shapes
- treasury permissions and the full flow
- the missing-credentials path
- webhook signature checks (including tampered raw bytes), deduplication, unmatched and rejected events, and ledger separation
- exact decimal parsing
