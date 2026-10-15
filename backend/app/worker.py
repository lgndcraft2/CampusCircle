"""Durable, repeatable deadline and reminder processing. Run as a separate worker."""
import argparse
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from . import emails
from .community import notify, notify_circle
from .db import SessionLocal
from .finance import finalize_loan, loan_view, transition
from .models import Circle, Loan, Reminder, User, now
from .security import aware


def tick():
    with SessionLocal.begin() as db:
        # Shared circle locks serialize this worker with financial API actions.
        circle_ids = list(db.scalars(select(Loan.circle_id).where(Loan.status.notin_(['repaid', 'rejected', 'expired', 'cancelled'])).distinct()))
        for circle_id in sorted(circle_ids):
            db.scalar(select(Circle).where(Circle.id == circle_id).with_for_update())
            for loan in db.scalars(select(Loan).where(Loan.circle_id == circle_id)):
                if loan.status == 'pending_vote' and aware(loan.deadline) <= now():
                    finalize_loan(db, loan, None)
                if loan.status in ('approved_awaiting_acceptance', 'approved_insufficient_funds', 'accepted_pending_checks', 'disbursement_pending', 'disbursement_failed') and aware(loan.acceptance_expires_at) <= now():
                    transition(db, loan, 'expired', None)
                    notify_circle(db, circle_id, 'Proposal expired', 'The acceptance window closed. Any reserved funds were released.')
                if loan.status == 'pending_vote' and aware(loan.deadline) <= now() + timedelta(hours=6):
                    key = f'vote-deadline:{loan.id}'
                    if not db.scalar(select(Reminder).where(Reminder.key == key)):
                        notify_circle(db, circle_id, 'Voting closes soon', f'Review {loan.purpose} before its voting deadline.')
                        db.add(Reminder(key=key))
                if loan.status in ('disbursed', 'repaying'):
                    borrower = db.get(User, loan.borrower_id)
                    for item in loan_view(db, loan, borrower)['schedule']:
                        due = datetime.fromisoformat(item['due_at'])
                        if item['paid_minor'] < item['amount_minor'] and due <= now() + timedelta(days=3):
                            overdue = due < now()
                            key = f'{"overdue" if overdue else "upcoming"}:{loan.id}:{item["number"]}'
                            if not db.scalar(select(Reminder).where(Reminder.key == key)):
                                notify(db, borrower.id, 'Repayment overdue' if overdue else 'Upcoming repayment', f'Instalment {item["number"]} for {loan.purpose} is {"overdue" if overdue else "due soon"}. Simulation only.', circle_id)
                                db.add(Reminder(key=key))


def deliver_emails():
    # Separate transaction: email retries never hold circle locks or roll back financial work.
    with SessionLocal.begin() as db:
        return emails.process_queue(db)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    while True:
        try:
            tick()
            deliver_emails()
        except Exception:
            logging.exception('Deadline worker failed; the transaction was rolled back.')
            if args.once:
                raise
        if args.once:
            break
        time.sleep(30)
