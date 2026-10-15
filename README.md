# CampusCircle

**Save together. Decide together. Grow together.**

CampusCircle helps university students in Nigeria run trusted savings and lending circles. Members record contributions, vote on loan proposals with fixed terms, and understand their commitments with a grounded financial copilot.

> **Money status.** The internal ledger is a **simulation**. Every simulated record is labelled and stored with `provenance = simulation`. An optional **B Moni sandbox** treasury per circle uses test tokens only. Its deposits are recorded in a *separate* ledger account and are never mixed with simulated funds. No real money moves.

## Features

| Area | What works |
| --- | --- |
| Accounts | Registration (name, phone, university, department, student email, password), email OTP verification, login, refresh rotation, logout, session list/revocation, password reset, profile and notification preferences |
| Circles | Create, discover (search/filter, paginated), join (open, approval, invitation link), roles (owner/admin/member), approve/reject/remove, ownership transfer, leave rules |
| Chat | Persistent, member-only circle chat, unread counts, author/admin removal (tombstones), 5-second polling |
| Savings | Goals, idempotent simulated contributions, reversals by compensating entry, ledger reconciliation |
| Loans | Requests, immutable versioned proposals, frozen voter snapshot, quorum and threshold, server-calculated results, exact-version borrower acceptance, insufficient-funds state, reserved funds, simulated disbursement, persisted instalments, repayments, simulated failed repayments |
| Copilot | Answers grounded in data the user is authorised to see. The built-in deterministic guide always works; optional Groq phrasing requires consent |
| B Moni | Sandbox treasury per circle (user and CNGN smart wallet), provider balance read, HMAC-verified and deduplicated webhooks |
| Email | Brevo transactional email with HTML and text templates, a delivery log, deduplication, and capped retries; a development outbox when Brevo is not configured |
| Participation | Explainable levels (Starter, Builder, Trusted, Circle Champion). Not a credit score; simulated money earns no points |

## Architecture

```
frontend/   React 19 + TypeScript (strict) + Vite + TanStack Query + React Hook Form/Zod + Radix dialog + Lucide
backend/    FastAPI + Pydantic + SQLAlchemy 2 + Alembic; PostgreSQL in deployment, SQLite for local/test
docs/       architecture, API, B Moni findings, security, demo guide
```

See [docs/architecture.md](docs/architecture.md).

## Prerequisites

- Python 3.12, Node.js 20+ (tested with Node 23), npm
- Optional: Docker and Docker Compose for the PostgreSQL stack

## Local setup (no Docker)

```bash
cp .env.example .env              # defaults run fully offline with SQLite + dev outbox
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements-dev.txt     # Windows; use .venv/bin/pip on macOS/Linux
cd backend
../.venv/Scripts/alembic upgrade head        # create the schema
../.venv/Scripts/python -m app.cli seed      # synthetic demo data, created through the real API
../.venv/Scripts/uvicorn app.main:app --port 8000
# another terminal, optional deadline/reminder/email worker:
../.venv/Scripts/python -m app.worker
# another terminal:
cd frontend && npm install && npm run dev    # http://127.0.0.1:5173
```

Demo accounts (all synthetic): `amina@`, `tobi@`, `zainab@`, `chidi@students.example.edu`, password `CampusCircle-demo-2026!`. The login page has a "Use synthetic demo details" shortcut when the dev outbox is on.

- Reset demo data: `python -m app.cli reset` (development only; drops the database, re-runs migrations, and re-seeds)
- Read verification codes in development: `python -m app.cli mail` (reads the private `.dev-mail/` folder; codes are never shown on the website)
- API docs: http://127.0.0.1:8000/api/v1/docs · health: `/api/v1/health`

## Docker Compose (PostgreSQL)

```bash
cp .env.example .env   # set APP_SECRET; leave ALLOW_SQLITE/DATABASE_URL, compose overrides them
docker compose up --build
docker compose exec backend python -m app.cli seed
# open http://localhost:8080
```

The Compose stack runs PostgreSQL 16, the API (with migrations on start), the worker, and nginx serving the built frontend. **Not verified here:** Docker was not available on the build machine.

## Environment variables

See [.env.example](.env.example). Summary:

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL`, `APP_SECRET`, `FRONTEND_URL` | yes | Production requires PostgreSQL, HTTPS origin, a strong secret |
| `ALLOW_SQLITE`, `DEVELOPMENT_OUTBOX`, `COOKIE_SECURE=false` | dev only | Rejected at startup in production |
| `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME` | optional | Without them, email uses the dev outbox or fails visibly |
| `GROQ_API_KEY`, `GROQ_MODEL` | optional | Without them, the copilot uses the built-in guide |
| `BMONI_API_KEY`, `BMONI_SIGNER_SEED`, `BMONI_WEBHOOK_SECRET` | optional | Sandbox only; base URL is pinned to `https://embedded-dev.bmoni.com` |

## Testing

```bash
cd backend && ../.venv/Scripts/python -m pytest -q          # unit + integration (providers mocked)
cd backend && ../.venv/Scripts/ruff check app tests          # lint
cd frontend && npx tsc -b && npm test                        # type check + Vitest
cd frontend && npx playwright install chromium && npx playwright test   # E2E: starts its own servers on ports 8010/5183
cd frontend && npm run build                                 # production build
```

## Integrations

- **Brevo:** `POST https://api.brevo.com/v3/smtp/email` with the `api-key` header. A returned `messageId` means *provider accepted*, not *delivered*.
- **Groq:** `POST https://api.groq.com/openai/v1/chat/completions`, default model `openai/gpt-oss-20b`, `include_reasoning=false`. The model has no tools.
- **B Moni:** see [docs/bmoni-integration.md](docs/bmoni-integration.md). Verified live against the sandbox: user creation, wallet provisioning, balance read.

## Troubleshooting

- `SQLite requires ALLOW_SQLITE=true`: set it in `.env` for local runs.
- `Security check failed`: the CSRF cookie is stale. Reload the page, or sign in again.
- `Origin is not permitted`: `FRONTEND_URL` must exactly match the browser origin, including `127.0.0.1` vs `localhost`.
- Email codes are missing: with the dev outbox, run `python -m app.cli mail`. Codes are never logged.
- `Too many attempts`: rate limits are stored in the database. Run `reset` locally, or wait 15 minutes.

## Known limitations

See the "Known limitations" section of [docs/security.md](docs/security.md) and the status table in [docs/demo-guide.md](docs/demo-guide.md). In brief: simulated ledger only, no real KYC or payouts, chat by polling with text only, no phone-change verification (no SMS provider), and legal pages are drafts.
