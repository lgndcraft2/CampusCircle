# Demo guide (about 5 minutes)

## Setup

Follow the README local setup, then run `python -m app.cli reset` from `backend/` for clean synthetic data. Every account uses the password `CampusCircle-demo-2026!`.

| Person | Role in the seed |
| --- | --- |
| Amina (`amina@students.example.edu`) | Owner of all three circles |
| Tobi | Borrower on the open proposal in **The Study Fund** |
| Zainab | Borrower on the disbursed loan in **Creators on Campus**; one instalment paid, one failed attempt |
| Chidi | Economics student, a member of no circle (use for the access-control demo) |

## Script

1. **Landing → Log in.** Use "Use synthetic demo details" to sign in as Amina. Point out the persistent *Simulation mode* banner.
2. **Dashboard.** Show recorded totals (labelled simulated), "Needs your attention", the participation level ("Not a credit score"), goals, and recent updates.
3. **The Study Fund → Loans.** Open "A laptop for final-year project":
   - Terms are fixed and fingerprinted.
   - Show the frozen voter count and the quorum and threshold.
   - Vote **Support**; it can't be changed.
   - **Calculate voting result**: the server decides.
4. **Sign in as Tobi** (Profile → Sign out). Open the proposal and **Accept these exact terms**. Funds are reserved.
5. **As Amina:** **Record simulated disbursement**. The repayment schedule appears with instalment statuses.
6. **Creators on Campus → the camera lens loan.** One instalment is *Paid*, one *Failed*. As Amina (an admin) you can see the private attempt history.
7. **Copilot** (Explain these terms):
   - Ask "What do I owe and when?" or "What is missing or unclear in this proposal?". Answers quote server numbers.
   - Choose a second proposal to compare.
   - The AI toggle shows Groq as unavailable unless configured.
8. **Transactions** tab: every row is labelled *Simulated*. Admins can reverse a contribution (a compensating entry) and run reconciliation.
9. **Sandbox treasury** panel: it shows "not configured", or, with B Moni keys, connects a sandbox wallet and reads the provider balance.
10. **Access control.** Sign in as Chidi and paste a Study Fund URL: the response is "Circle not found or you do not have access."

## Status by capability

| Capability | Status |
| --- | --- |
| Auth, sessions, OTP, reset, profile | Implemented and tested (pytest + Playwright) |
| Circles, roles, invitations, membership | Implemented and tested |
| Chat (polling, text) | Implemented and tested; no WebSockets, voice notes, or attachments |
| Notifications (in-app) | Implemented and tested |
| Ledger, contributions, reversals, reconciliation | Implemented and tested (simulation) |
| Loan workflow, voting, acceptance, disbursement, instalments, repayments | Implemented and tested (simulation) |
| Brevo email | Implemented; tested with mocked Brevo responses; **not sent live** (no key provided) |
| Groq copilot | Implemented; tested with a mocked Groq API; **not called live** (no key provided) |
| Built-in deterministic copilot | Implemented and tested |
| B Moni sandbox treasury (user + wallet + balance) | Implemented; unit-tested; **verified live** in the sandbox |
| B Moni webhooks | Implemented and unit-tested; **live delivery not observed** |
| B Moni KYC, NUBAN, transfers, payouts | Not implemented (see bmoni-integration.md) |
| Participation levels | Implemented and tested |
| PostgreSQL migrations | Applied to Neon PostgreSQL 16 (tables and triggers confirmed) |
| Docker Compose | Written; **not executed** (Docker unavailable on the build machine) |
| Phone change verification | Blocked: no SMS provider; fails safely |
