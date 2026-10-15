import { expect, request, test } from '@playwright/test';
import { apiStudent, codeFor, ORIGIN, PASSWORD, unique, type Student } from './helpers';

interface Loan { id: string; version: number; terms_hash: string; status: string; total_minor: number; schedule: { amount_minor: number; status: string }[] }
const naira = (minor: number) => `₦${Math.floor(minor / 100).toLocaleString('en-US')}.${String(minor % 100).padStart(2, '0')}`;

test('a full circle journey: register to repayment, copilot, and access control', async ({ page }) => {
  const owner = unique('ada');
  // 1. Register through the UI and verify with the code from the private development outbox.
  await page.goto('/register');
  await page.getByLabel('Full name').fill('Ada Obi');
  await page.getByLabel('Phone number').fill('+2348031234500');
  await page.getByLabel('University').selectOption('Demo University');
  await page.getByLabel('Department').fill('Computer Science');
  await page.getByLabel('Student email').fill(owner);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Create your account' }).click();
  await expect(page).toHaveURL(/\/verify\?email=/);
  await page.getByLabel('6-digit code').fill(await codeFor(owner));
  await page.getByRole('button', { name: 'Verify email' }).click();
  await expect(page).toHaveURL(/\/login$/);

  // 2. Log in. A new account sees an honest empty state.
  await page.getByLabel('Student email').fill(owner);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Log in' }).click();
  await expect(page.getByRole('heading', { name: /Hey Ada/ })).toBeVisible();
  await expect(page.getByText('Email verified', { exact: true })).toBeVisible();
  await expect(page.getByText('You don’t have a circle yet', { exact: false })).toBeVisible();

  // 3. Create a circle.
  await page.getByRole('link', { name: 'Create a circle' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Circle name').fill('E2E Study Circle');
  await dialog.getByLabel('Description').fill('A circle created by the automated end-to-end test.');
  await dialog.getByRole('button', { name: 'Create a circle' }).click();
  await expect(page.getByRole('heading', { name: 'E2E Study Circle' })).toBeVisible();
  const circleId = page.url().split('/').pop()!;

  // 4. Invite a member through the UI; two other students join with real API calls.
  await page.getByRole('button', { name: 'Members' }).click();
  await page.getByRole('button', { name: 'Invite someone' }).click();
  await page.getByRole('button', { name: 'Create invitation link' }).click();
  const link = await page.locator('#invite-link').inputValue();
  const token = link.split('/invite/')[1];
  await page.keyboard.press('Escape');
  const borrower = await apiStudent(await request.newContext({ baseURL: ORIGIN }), 'Bola Ade');
  const voter = await apiStudent(await request.newContext({ baseURL: ORIGIN }), 'Chi Eze');
  const outsider = await apiStudent(await request.newContext({ baseURL: ORIGIN }), 'Dayo Uche');
  await borrower.post('/invitations/accept', { token });
  await voter.post('/invitations/accept', { token });

  // 5. Record a simulated contribution in the UI.
  await page.getByRole('button', { name: 'Contribute' }).first().click();
  await page.getByRole('dialog').getByLabel('Amount (NGN)').fill('10000');
  await page.getByRole('button', { name: 'Record simulated contribution' }).click();
  await page.getByRole('button', { name: 'Transactions' }).click();
  await expect(page.getByText('₦10,000.00').first()).toBeVisible();
  await expect(page.getByText('Simulated').first()).toBeVisible();

  // 6. The borrower requests a loan; the owner publishes explicit terms in the UI.
  await borrower.post(`/circles/${circleId}/loan-requests`, { principal_minor: 500000, purpose: 'Textbooks for second semester', explanation: 'Requested by the end-to-end test borrower.', frequency: 'monthly', instalment_count: 3 });
  await page.getByRole('button', { name: 'Loans' }).click();
  await page.getByRole('button', { name: 'Set proposal terms' }).click();
  await page.getByRole('dialog').getByLabel('Flat interest rate (basis points)').fill('500');
  await page.getByRole('button', { name: 'Publish fixed terms & open voting' }).click();
  await expect(page.getByText('PROPOSAL VERSION 1 · FIXED TERMS')).toBeVisible();
  const loanId = page.url().split('/').pop()!;

  // 7. Votes from separate authorised accounts. The borrower cannot vote.
  await expect(page.getByText('Total repayment').first()).toBeVisible();
  await page.getByRole('button', { name: 'Support' }).click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await expect(page.getByText('Your support vote is recorded.', { exact: false }).first()).toBeVisible();
  const forbidden = await borrower.api.post(`/api/v1/loans/${loanId}/votes`, { data: { choice: 'support' }, headers: borrower.headers() });
  expect(forbidden.status()).toBe(403);
  await voter.post(`/loans/${loanId}/votes`, { choice: 'support' });

  // 8. The server calculates the outcome.
  await page.getByRole('button', { name: 'Calculate voting result' }).click();
  await expect(page.getByText('Approved awaiting acceptance').first()).toBeVisible();

  // 9. The borrower accepts the exact version; the owner records a simulated disbursement.
  const approved = await borrower.get<Loan>(`/loans/${loanId}`);
  expect(approved.total_minor).toBe(525000);
  await borrower.post(`/loans/${loanId}/accept`, { version: approved.version, terms_hash: approved.terms_hash }, 'e2e-accept-0001');
  await page.reload();
  await page.getByRole('button', { name: 'Record simulated disbursement' }).click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await expect(page.getByText('Disbursed').first()).toBeVisible();
  await expect(page.getByRole('table', { name: /Repayment schedule/ }).getByText('Upcoming').first()).toBeVisible();

  // 10. A failed then successful simulated repayment, reflected in authoritative state and private notices.
  const disbursed = await borrower.get<Loan>(`/loans/${loanId}`);
  const first = disbursed.schedule[0].amount_minor;
  const failed = await borrower.post<Loan>(`/loans/${loanId}/repayments`, { amount_minor: first, outcome: 'failure' }, 'e2e-repay-fail-01');
  expect(failed.schedule[0].status).toBe('failed');
  const paid = await borrower.post<Loan>(`/loans/${loanId}/repayments`, { amount_minor: first }, 'e2e-repay-ok-0001');
  expect(paid.schedule[0].status).toBe('paid');
  expect(paid.status).toBe('repaying');
  const notices = await borrower.get<{ items: { title: string }[] }>('/notifications?limit=50');
  expect(notices.items.map(n => n.title)).toEqual(expect.arrayContaining(['Repayment did not go through', 'Simulated repayment recorded', 'Your repayment schedule is ready']));
  const voterNotices = await voter.get<{ items: { title: string }[] }>('/notifications?limit=50');
  expect(voterNotices.items.map(n => n.title)).not.toContain('Repayment follow-up needed');
  await page.reload();
  await expect(page.getByRole('table', { name: /Repayment schedule/ }).getByText('Paid', { exact: true })).toBeVisible();

  // 11. Copilot explains the resulting terms from server data.
  await page.getByRole('link', { name: 'Explain these terms' }).click();
  await page.getByLabel('What would you like to understand?').fill('Explain these terms and what is left to pay');
  await page.getByRole('button', { name: 'Ask Copilot' }).click();
  await expect(page.getByText('Built-in guide · not AI')).toBeVisible();
  await expect(page.locator('.guide-answer').getByText(naira(525000), { exact: false })).toBeVisible();

  // 12. Someone outside the circle cannot see its private information.
  for (const path of [`/circles/${circleId}`, `/loans/${loanId}`, `/circles/${circleId}/messages`, `/circles/${circleId}/transactions`]) {
    await outsider.get(path, 404);
  }
  const outsiderCopilot = await outsider.api.post('/api/v1/copilot', { data: { question: 'Explain', loan_id: loanId }, headers: outsider.headers() });
  expect(outsiderCopilot.status()).toBe(404);
  await Promise.all([borrower, voter, outsider].map((s: Student) => s.api.dispose()));
});
