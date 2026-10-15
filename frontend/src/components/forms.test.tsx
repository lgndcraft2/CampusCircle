import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { InstalmentStatus, ProgressCard, SmartForm, Transactions } from './ui';
import type { Progress, Transaction } from '../types';

describe('SmartForm validation', () => {
  it('blocks submission and shows field errors until required values are present', async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<SmartForm fields={[{ name: 'email', label: 'Student email', type: 'email' }]} onSubmit={submit} submit="Continue" />);
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText('Student email is required.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Student email'), { target: { value: 'not-an-email' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Student email'), { target: { value: 'ada@students.example.edu' } });
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ email: 'ada@students.example.edu' }));
  });
  it('defaults selects to their first option and treats optional fields as optional', async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<SmartForm fields={[{ name: 'purpose', label: 'Purpose', options: [{ value: 'Books', label: 'Books' }] }, { name: 'department', label: 'Department', required: false, options: [{ value: '', label: 'All' }, { value: 'CS', label: 'CS' }] }]} onSubmit={submit} submit="Save" />);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ purpose: 'Books', department: '' }));
  });
  it('shows server errors without crashing', async () => {
    render(<SmartForm fields={[{ name: 'name', label: 'Name' }]} onSubmit={() => Promise.reject(new Error('Circle not found or you do not have access.'))} submit="Go" />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Circle not found');
  });
});

describe('Financial provenance and status display', () => {
  const base: Transaction = { id: '1', user_id: 'u', circle_id: 'c', kind: 'contribution', amount_minor: 1000, currency: 'NGN', status: 'simulated_confirmed', provenance: 'simulation', created_at: '2026-10-15T10:00:00Z', goal_id: null, loan_id: null, reversal_of: null, notice: '' };
  it('labels simulated and sandbox-confirmed rows differently', () => {
    render(<Transactions items={[base, { ...base, id: '2', kind: 'sandbox_deposit', provenance: 'bmoni_sandbox', status: 'provider_confirmed' }]} />);
    expect(screen.getByText('Simulated')).toBeInTheDocument();
    expect(screen.getByText('Sandbox confirmed')).toBeInTheDocument();
  });
  it('shows an empty state with no transactions', () => {
    render(<Transactions items={[]} />);
    expect(screen.getByText('Your story starts here')).toBeInTheDocument();
  });
  it('renders instalment statuses in words', () => {
    render(<InstalmentStatus status="partially_paid" />);
    expect(screen.getByText('Partially paid')).toBeInTheDocument();
  });
  it('explains participation levels and never calls them a credit score', () => {
    const progress: Progress = { points: 15, level: 'Starter', next_level: 'Builder', next_level_at: 40, breakdown: [{ event: 'vote_cast', label: 'Took part in a circle vote', occurrences: 3, counted: 3, points: 15 }], practice: { simulated_contributions: 2, simulated_repayments: 0 }, levels: [{ name: 'Starter', from_points: 0 }, { name: 'Builder', from_points: 40 }], notice: 'Participation levels are not a credit score.' };
    render(<MemoryRouter><ProgressCard progress={progress} /></MemoryRouter>);
    expect(screen.getByText('Not a credit score')).toBeInTheDocument();
    expect(screen.getByText('Took part in a circle vote')).toBeInTheDocument();
    expect(screen.getByText(/2 contributions/)).toBeInTheDocument();
  });
});
