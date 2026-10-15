# CampusCircle implementation checklist

Repository inspection: the workspace contained only `Codex.md`; there is no existing application or Git repository to preserve.

## Plan

1. Scaffold React/Vite and FastAPI/SQLAlchemy, configuration, migrations, Docker, and local development.
2. Implement persistent cookie sessions, CSRF, verification/reset delivery, profile, university policy, and rate limits.
3. Implement circles, per-circle roles, invitations, membership, savings goals, chat, and notifications.
4. Implement an integer-minor-unit double-entry simulation ledger, immutable proposals, voting, borrower acceptance, disbursement, and repayments.
5. Connect a responsive lavender/indigo interface to real APIs, including public and account flows.
6. Add synthetic seed data, meaningful security/financial tests, browser tests, setup documentation, and an honest verification report.

## Decisions filling gaps

- Development financial mode is simulation only; every financial result retains simulation provenance. B Moni is optional and unavailable until documented sandbox credentials/capabilities exist. No invented provider endpoints.
- PostgreSQL is the deployment database. Explicit SQLite development/test mode allows local execution where Docker/PostgreSQL is absent; it does not prove PostgreSQL concurrency behavior.
- Money is NGN integer kobo. Interest is a disclosed flat rate in basis points, rounded half up; instalment remainders go to the earliest instalments.
- Borrowers cannot vote. Eligible voters are frozen when a proposal opens. Votes cannot change. Finalization occurs at deadline or when all eligible voters voted; quorum uses all eligible voters, approval uses cast votes, ties reject.
- Circle balances and savings progress are calculated on the server from ledger entries. Circle row locks serialize membership and financial mutations on PostgreSQL; SQLite writes use BEGIN IMMEDIATE.
- Approval never disburses. Borrower acceptance and disbursement are separate idempotent operations. No withdrawal of pooled contributions is offered; members with outstanding loans cannot leave. Rules disclose pooled funds.
- Chat uses authenticated polling, plain text only, author/admin tombstone deletion, membership checks and pagination. Voice notes are deferred unless reliably supported.
- Brevo email is configurable. Explicit development outbox is filesystem-only and disabled in production. No public OTP endpoint. No SMS provider means phone changes fail safely rather than claiming phone verification.
- Financial education is available without a model and labelled deterministic. Optional AI uses only an explicitly submitted question and structured terms, never private circle messages, and fails visibly when unavailable.
- Deployment and public release are separate from this local repository build.

## Progress (updated 2026-10-15, session 2)

- [x] Inspect repository and read brief.
- [x] Scaffold and install dependencies.
- [x] Backend vertical slices (auth, circles, chat, ledger, loans, instalments, repayment attempts).
- [x] Versioned API `/api/v1`, request IDs, JSON logs, consistent error body.
- [x] Integrations: Brevo (mocked tests), Groq (mocked tests), B Moni sandbox (live-verified user/wallet/balance; webhooks unit-tested).
- [x] Gamification (simulated money earns no points), extended dashboard.
- [x] Connected frontend + styles.css (was missing), treasury panel, progress card, copilot compare.
- [x] Automated verification: 37 pytest, 12 Vitest, 2 Playwright (desktop journey + mobile layout), ruff, tsc, build, pip-audit, npm audit.
- [x] Documentation (README, docs/*.md), .env.example, Dockerfiles, docker-compose.

## Open items

- PostgreSQL migrations/triggers and Docker Compose never executed (no Docker/PostgreSQL locally).
- Brevo/Groq not called live (no keys). B Moni webhook delivery not observed live (needs partnerId + public URL).
- No B Moni KYC/NUBAN/payouts (BVN collection forbidden by brief). No SMS provider for phone changes.
- Chat is polling-only; no voice notes/attachments. No ESLint config (tsc strict used instead).
