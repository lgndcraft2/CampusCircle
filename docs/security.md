# Security and privacy

## Threat model (summary)

| Threat | Mitigation |
| --- | --- |
| Account takeover | Argon2 password hashing; login throttled per IP and per account; generic login errors; constant-time dummy hash for unknown emails |
| Session theft / CSRF | Opaque random tokens stored only as SHA-256 hashes; HttpOnly `cc_access`/`cc_refresh` cookies scoped to `/api`; double-submit CSRF token header checked on every unsafe method; Origin must equal `FRONTEND_URL`; SameSite=Lax; `COOKIE_SECURE` required in production |
| Session replay | Refresh rotates access, refresh, and CSRF tokens; old refresh token rejected; password reset revokes all sessions; per-session revocation in the profile |
| OTP brute force / reuse | 6-digit codes from `secrets`; HMAC-hashed with `APP_SECRET`; 10-minute expiry; 5 attempts; one-time use; superseded codes invalidated; 60-second resend cooldown; IP and email throttles; never logged or returned |
| Account enumeration | Register, resend, and forgot-password return generic messages |
| IDOR / cross-circle access | Every circle-scoped query goes through `member()`, which checks active membership and role. Loans, messages, goals, transactions, treasury, and the copilot all resolve the circle first. Non-members get 404 |
| Mass assignment | Pydantic models use `extra='forbid'`; profile edits accept only name and department |
| SQL injection | SQLAlchemy parameter binding only |
| XSS | React escapes output; the API never returns HTML; email templates escape all values; CSP `default-src 'none'` on API responses |
| Financial tampering | Journals, ledger entries, votes, audit events, and repayment attempts are append-only (ORM guard plus database triggers). Loan terms are immutable after publication (ORM guard plus trigger). PostgreSQL has a deferred trigger rejecting unbalanced journals. Integer kobo only; floats rejected by strict validation |
| Duplicate operations | `Idempotency-Key` on contributions, reversals, acceptance, disbursement, and repayments, each with a request fingerprint; reused key with different content gives 409 |
| Race conditions | Circle row lock (`SELECT … FOR UPDATE`) serialises financial and membership mutations on PostgreSQL; SQLite uses `BEGIN IMMEDIATE`; unique constraints back up vote and idempotency rules |
| Webhook forgery / replay | HMAC-SHA256 over raw body, constant-time compare, unique `(provider, event_id)` |
| AI prompt injection / leakage | Copilot facts are built server-side from authorised data only; member names removed; chats never sent; question marked untrusted; the model has no tools and cannot act; consent required; only metadata stored |
| Secrets exposure | Secrets live only in backend environment variables; nothing provider-related reaches Vite; logs record method, path, status, and duration only; the B Moni adapter redacts key-like strings |
| Oversized requests | 256 KB request limit; field length limits on every input |

## Permission model

- **Platform:** no global admin role exists. Circle owners have no platform powers.
- **Owner:** circle settings, roles, ownership transfer, and the sandbox treasury. Plus everything an admin can do.
- **Admin:** approve, reject, or remove members (not other admins); invitations; goals; publish proposals; record simulated disbursement; reverse contributions; reconciliation; view repayment attempts; read the treasury balance.
- **Member:** view the circle's finances, chat, contribute, request loans, vote when in the frozen voter snapshot, and accept their own loan terms.
- **Borrower:** cannot vote on their own proposal. Only they can accept or repay.
- **Nobody** can edit confirmed ledger rows, change active proposal terms, vote for someone else, or skip acceptance.

## Data handling

- **Stored:** registration details, hashed credentials, memberships, chat, votes, proposals, ledger records, notifications, email delivery metadata, AI usage metadata.
- **Not stored:** BVN, ID documents, card data, AI questions and answers, verification codes in plain text.
- Repayment failures are shown only to the borrower and the circle's admins.
- Chat deletion replaces content with a tombstone. Financial and audit history is permanent and corrected only by compensating entries.
- **Retention:** no automated deletion policy yet. An operator policy is required before public launch.

## Dependency checks

`pip-audit -r backend/requirements.txt` and `npm audit` (see the final report for results on the build machine).

## Known limitations

- The ledger is simulated. B Moni is sandbox-only, and KYC and payouts are not implemented.
- The email-domain check proves access to an inbox, not current enrolment.
- Phone changes are disabled: there is no SMS provider.
- Rate limiting is database-backed per process. It is sufficient for the demo; use Redis or an edge limiter at scale.
- Chat uses 5-second polling, not WebSockets. Voice notes and attachments are not implemented.
- The sandbox treasury owner key is derived on the server (custodial) and is unsuitable for real funds.
- PostgreSQL-specific triggers were written but **not executed on PostgreSQL** during this build: no PostgreSQL or Docker was available. All automated tests ran on SQLite.
- Legal pages are drafts and have had no legal review. CampusCircle is not a licensed financial service.
