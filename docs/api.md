# API (v1)

Base path: `/api/v1`. The live OpenAPI spec is at `/api/v1/openapi.json`, with an interactive version at `/api/v1/docs`.

## Conventions

- **Errors** are `{"detail": <string | field list>, "request_id": "<id>"}`. Every response carries an `X-Request-ID` header.
- **Unsafe methods** need the `X-CSRF-Token` header (the value of the `cc_csrf` cookie) and an allowed `Origin`.
- **Money** is integer kobo (`*_minor`, NGN). Floats are rejected.
- **Idempotency.** `Idempotency-Key` (8–100 characters) is required on contributions, reversals, accept, disburse, and repayments. Repeating the same key with the same body returns the original result; a different body returns 409.
- **Collections** paginate with `page` and `limit` and return `{items,total,page,limit}`.
- **Status codes:** 401 = signed out, 403 = role not allowed, 404 = not found or not a member, 409 = state conflict, 422 = validation, 429 = throttled, 502/503 = provider unavailable (nothing claimed).

## Endpoints

| Method & path | Who | Purpose |
| --- | --- | --- |
| GET `/health` | public | DB check + integration availability |
| GET `/universities` | public | Configured directory |
| POST `/auth/register` · `/auth/verify` · `/auth/resend` · `/auth/login` · `/auth/refresh` · `/auth/logout` | public / session | Account lifecycle |
| POST `/auth/forgot-password` · `/auth/reset-password` | public | Reset via email code |
| GET/PATCH `/me` · PATCH `/me/preferences` · GET `/me/sessions` · DELETE `/me/sessions/{id}` · POST `/me/phone-change` (503) | session | Profile |
| GET `/me/progress` | session | Participation level and reasons |
| GET `/dashboard` | session | Personal summary |
| GET/POST `/circles` · GET/PATCH `/circles/{id}` | member / owner | Discover (`scope=discover`), list, create, settings |
| POST `/circles/{id}/join` · `/leave` · GET `/members` · POST `/members/{mid}` | per role | Membership |
| POST/GET `/circles/{id}/invitations` · DELETE `/invitations/{iid}` · POST `/invitations/accept` | admin / invitee | Invitations |
| GET/POST `/circles/{id}/goals` | member / admin | Savings goals |
| GET/POST `/circles/{id}/messages` · POST `/messages/read` · DELETE `/messages/{mid}` | member | Chat |
| GET `/notifications` · POST `/notifications/{id}/read` | session | In-app notices |
| GET `/circles/{id}/activity` | member | Audit trail |
| GET `/circles/{id}/transactions` · POST `/contributions` · POST `/transactions/{jid}/reverse` | member / admin | Ledger |
| GET `/circles/{id}/reconciliation` | admin | Balance + instalment checks |
| GET/POST `/circles/{id}/loan-requests` · POST `/circles/{id}/proposals` · GET `/circles/{id}/loans` | member / admin | Loans |
| GET `/loans` · GET `/loans/{id}` | member | Loans in your circles |
| POST `/loans/{id}/votes` · `/finalize` · `/accept` · `/disburse` · `/repayments` · `/cancel` | per role | State machine |
| GET `/loans/{id}/repayment-attempts` | borrower / admin | Private repayment history |
| POST `/copilot` | session | `{question, loan_id?, compare_loan_id?, use_ai, consent}` |
| GET/POST `/circles/{id}/treasury` · GET `/treasury/balance` | member / owner / admin | B Moni sandbox |
| POST `/webhooks/bmoni` | B Moni (HMAC) | Provider events |
