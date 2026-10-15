# Architecture

```
Browser (React SPA) ──/api/v1 (cookies + CSRF header)──▶ FastAPI ──SQLAlchemy──▶ PostgreSQL / SQLite
                                                         │
                                 worker (python -m app.worker): deadlines, reminders, email queue
                                                         │
                     Brevo (email) · Groq (AI phrasing) · B Moni sandbox (treasury, webhooks)
```

## Backend modules (`backend/app`)

| Module | Responsibility |
| --- | --- |
| `config.py` | Settings, runtime guards (no SQLite without opt-in, sandbox-only B Moni, production checks) |
| `db.py` | Engine, session per request (commit or rollback), SQLite `BEGIN IMMEDIATE` |
| `models.py` | All tables (UTC timestamps, integer kobo, explicit currency) |
| `security.py` | Password hashing, sessions, CSRF, OTP, throttling, audit helper |
| `auth.py` | Registration, verification, login, refresh, logout, reset, profile, sessions |
| `community.py` | Circles, membership, invitations, goals, chat, notifications, balances |
| `finance.py` | Ledger posting, contributions, reversals, loan state machine, proposals, votes, acceptance, disbursement, instalments, repayments, reconciliation |
| `copilot.py` | Fact building from authorised data, intent detection, deterministic guide, Groq provider |
| `gamification.py` | Configurable participation levels |
| `bmoni.py` / `integrations.py` | B Moni adapter, circle treasury routes, webhook receiver |
| `emails.py` | Templates, Brevo sender, delivery log, queue |
| `worker.py` | Voting deadlines, acceptance expiry, reminders, email delivery |
| `cli.py` | `seed`, `reset`, `mail` (development only) |

## Ledger

Double-entry. Each `journals` row has at least two `ledger_entries` that sum to zero.

| Operation | Debit | Credit |
| --- | --- | --- |
| Contribution | `cash` | `external_simulation` |
| Reversal | `external_simulation` | `cash` |
| Disbursement | `loan_receivable` | `cash` |
| Repayment | `cash` | `loan_receivable` (principal) + `finance_income` (interest and fees) |
| Sandbox deposit | `bmoni_sandbox_cash` | `bmoni_sandbox_clearing` |

`available = cash − principal of loans in disbursement_pending`. Instalment allocations are checked against repayment journals by `/reconciliation`.

## Loan state machine (`finance.TRANSITIONS`, enforced by `transition()`)

```
pending_vote ─▶ rejected | approved_awaiting_acceptance | approved_insufficient_funds | cancelled
approved_awaiting_acceptance ─▶ accepted_pending_checks | expired | cancelled
approved_insufficient_funds ─▶ accepted_pending_checks | expired | cancelled
accepted_pending_checks ─▶ disbursement_pending | approved_insufficient_funds | expired
disbursement_pending ─▶ disbursed | disbursement_failed | expired
disbursement_failed ─▶ accepted_pending_checks | expired
disbursed ─▶ repaying | repaid
repaying ─▶ repaying | repaid
```

Every transition writes an audit event. The client cannot set a status directly.

## Voting policy

- Eligible voters are the active members other than the borrower, frozen when the proposal opens.
- Each eligible voter gets one vote, which cannot be changed.
- The proposal finalises at the deadline, or earlier once everyone has voted.
- Quorum is measured against all eligible voters.
- The threshold is measured against cast votes. Ties reject.

## Money rules

- Flat interest is `round_half_up(principal × bps / 10000)`.
- The total is principal plus interest plus fees.
- The total is split across instalments; leftover kobo go to the earliest instalments.
- Repayments are applied to the earliest instalments, principal first.

## Frontend (`frontend/src`)

- `api.ts` is the single fetch client. It handles the CSRF header, transparent refresh on 401, error normalisation, and money helpers.
- `components/ui.tsx` holds the shared components: forms, dialogs, badges, transactions, progress, pagination, and states.
- `pages/` contains Public (landing, auth, legal), Workspace (shell, dashboard, profile, notifications, copilot, invite), Circles, Loans, and Chat.

## Migrations

- `0001` creates the original tables, the append-only and immutability triggers, and PostgreSQL's balanced-journal constraint trigger.
- `0002` adds the integration tables and the append-only trigger on repayment attempts.
- Both use `metadata.create_all(tables=[…])` for the named tables. Future schema changes must use explicit `op.*` migrations.
