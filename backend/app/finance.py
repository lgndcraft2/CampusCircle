import calendar
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from . import schemas as s
from .community import active_members, balances, member, notify, notify_circle
from .db import get_db
from .models import Audit, Entry, Goal, Instalment, Journal, Loan, LoanRequest, Operation, RepaymentAttempt, User, Vote, now
from .security import audit, aware, digest, fail, session_user

router = APIRouter(prefix='/api/v1')

TRANSITIONS = {
    'pending_vote': {'rejected', 'approved_awaiting_acceptance', 'approved_insufficient_funds', 'cancelled'},
    'approved_awaiting_acceptance': {'accepted_pending_checks', 'expired', 'cancelled'},
    'approved_insufficient_funds': {'accepted_pending_checks', 'expired', 'cancelled'},
    'accepted_pending_checks': {'disbursement_pending', 'approved_insufficient_funds', 'expired'},
    'disbursement_pending': {'disbursed', 'disbursement_failed', 'expired'},
    'disbursement_failed': {'accepted_pending_checks', 'expired'},
    'disbursed': {'repaying', 'repaid'}, 'repaying': {'repaying', 'repaid'},
    'repaid': set(), 'rejected': set(), 'expired': set(), 'cancelled': set(),
}


def transition(db: Session, loan: Loan, target: str, actor_id: str | None):
    if target not in TRANSITIONS.get(loan.status, set()):
        fail(409, f'Cannot move a loan from {loan.status} to {target}.')
    previous = loan.status
    loan.status = target
    audit(db, actor_id, 'loan.transition', loan.id, loan.circle_id, previous=previous, status=target, version=loan.version)
    db.flush()


@event.listens_for(Loan, 'before_update')
def immutable_terms(_, __, target):
    mutable = {'status', 'accepted_at', 'accepted_version'}
    if any(attr.history.has_changes() and attr.key not in mutable for attr in inspect(target).attrs):
        raise ValueError('Proposal terms are immutable; create a new version.')


def immutable_record(*_):
    raise ValueError('Financial and audit records are append-only; use compensating entries.')


for model in (Journal, Entry, Audit, Vote, RepaymentAttempt):
    event.listen(model, 'before_update', immutable_record)
    event.listen(model, 'before_delete', immutable_record)


def key_check(key: str | None):
    if not key or not 8 <= len(key) <= 100:
        fail(422, 'Provide an Idempotency-Key between 8 and 100 characters.')
    return key


def fingerprint(**data):
    return digest(json.dumps(data, sort_keys=True, separators=(',', ':')))


NOTICES = {'simulation': 'SIMULATED — no real money was transferred.',
           'bmoni_sandbox': 'B Moni SANDBOX — confirmed by a signed provider webhook. Test tokens only; no real money.'}


def journal_view(row: Journal, db: Session):
    reversed_by = db.scalar(select(Journal.id).where(Journal.reversal_of == row.id))
    return dict(id=row.id, user_id=row.user_id, circle_id=row.circle_id, kind=row.kind, amount_minor=row.amount_minor,
                currency=row.currency, status='simulated_reversed' if reversed_by else row.status, provenance=row.provenance,
                created_at=row.created_at, goal_id=row.goal_id, loan_id=row.loan_id, reversal_of=row.reversal_of,
                notice=NOTICES.get(row.provenance, NOTICES['simulation']))


def post_journal(db: Session, circle_id: str, user_id: str, key: str, kind: str, amount: int, lines: list[tuple[str, int]], goal_id=None, loan_id=None, reversal_of=None,
                 status='simulated_confirmed', provenance='simulation'):
    key_check(key)
    fp = fingerprint(user_id=user_id, kind=kind, amount=amount, goal_id=goal_id, loan_id=loan_id, reversal_of=reversal_of)
    existing = db.scalar(select(Journal).where(Journal.circle_id == circle_id, Journal.idempotency_key == key))
    if existing:
        if existing.fingerprint != fp:
            fail(409, 'This idempotency key was already used for a different operation.')
        return existing
    if type(amount) is not int or amount <= 0 or len(lines) < 2 or sum(v for _, v in lines) != 0 or any(type(v) is not int for _, v in lines):
        raise ValueError('A journal requires positive integer money and balanced integer entries.')
    row = Journal(circle_id=circle_id, user_id=user_id, idempotency_key=key, kind=kind, amount_minor=amount, fingerprint=fp, goal_id=goal_id, loan_id=loan_id, reversal_of=reversal_of,
                  status=status, provenance=provenance)
    db.add(row)
    db.flush()
    for account, value in lines:
        if value:
            db.add(Entry(journal_id=row.id, circle_id=circle_id, account=account, amount_minor=value))
    audit(db, user_id, f'ledger.{kind}', row.id, circle_id, amount_minor=amount, provenance=provenance)
    db.flush()
    return row


def add_months(date, months):
    month = date.month - 1 + months
    year, month = date.year + month // 12, month % 12 + 1
    return date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1]))


def calculate_terms(principal: int, interest_bps: int, fees: int, count: int, frequency: str, anchor):
    interest = (principal * interest_bps + 5000) // 10000
    total = principal + interest + fees
    base, remainder = divmod(total, count)
    if base < 1:
        fail(422, 'There must be at least one kobo per instalment.')
    schedule = []
    for index in range(count):
        due = anchor + timedelta(weeks=index + 1) if frequency == 'weekly' else add_months(anchor, index + 1)
        schedule.append({'number': index + 1, 'amount_minor': base + (index < remainder), 'due_at': due.isoformat()})
    return interest, total, schedule


def create_proposal(db: Session, circle_id: str, user: User, data: s.ProposalInput):
    req = db.get(LoanRequest, data.request_id)
    if not req or req.circle_id != circle_id:
        fail(404, 'Loan request not found.')
    member(db, circle_id, db.get(User, req.borrower_id))
    previous = db.scalars(select(Loan).where(Loan.request_id == req.id).order_by(Loan.version.desc())).all()
    if previous and previous[0].status not in ('cancelled', 'rejected', 'expired'):
        fail(409, 'Close the previous proposal before creating a new version.')
    eligible = sorted(m.user_id for m in active_members(db, circle_id) if m.user_id != req.borrower_id)
    if not eligible:
        fail(422, 'At least one other active member is needed to vote.')
    deadline = now() + timedelta(hours=data.voting_hours)
    expiry = deadline + timedelta(days=7)
    interest, total, schedule = calculate_terms(req.principal_minor, data.interest_bps, data.fees_minor, data.instalment_count, data.frequency, expiry)
    terms = dict(circle_id=circle_id, request_id=req.id, borrower_id=req.borrower_id, author_id=user.id, version=previous[0].version + 1 if previous else 1,
                 principal_minor=req.principal_minor, interest_bps=data.interest_bps, interest_minor=interest, fees_minor=data.fees_minor, total_minor=total,
                 frequency=data.frequency, schedule=schedule, purpose=req.purpose, conditions=data.conditions, eligible_voters=eligible,
                 quorum_bps=data.quorum_bps, threshold_bps=data.threshold_bps, deadline=deadline, acceptance_expires_at=expiry)
    hashed = {k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in terms.items()}
    loan = Loan(**terms, terms_hash=fingerprint(**hashed))
    req.status = 'proposed'
    db.add(loan)
    db.flush()
    audit(db, user.id, 'proposal.opened', loan.id, circle_id, version=loan.version, terms_hash=loan.terms_hash)
    notify_circle(db, circle_id, 'A proposal needs your voice', f'Vote on {req.purpose}. Terms are fixed for version {loan.version}.')
    return loan


def loan_view(db: Session, loan: Loan, user: User):
    votes = db.scalars(select(Vote).where(Vote.loan_id == loan.id)).all()
    supports = sum(v.choice == 'support' for v in votes)
    paid = db.scalar(select(func.coalesce(func.sum(Journal.amount_minor), 0)).where(Journal.loan_id == loan.id, Journal.kind == 'repayment'))
    data = {key: getattr(loan, key) for key in ('id', 'circle_id', 'request_id', 'borrower_id', 'version', 'principal_minor', 'interest_bps', 'interest_minor', 'fees_minor', 'total_minor', 'frequency', 'purpose', 'conditions', 'quorum_bps', 'threshold_bps', 'deadline', 'acceptance_expires_at', 'status', 'accepted_at', 'accepted_version', 'terms_hash', 'provenance')}
    rows = db.scalars(select(Instalment).where(Instalment.loan_id == loan.id).order_by(Instalment.number)).all()
    if rows:
        schedule = [dict(number=i.number, amount_minor=i.amount_minor, due_at=aware(i.due_at).isoformat(), paid_minor=i.paid_minor,
                         status=instalment_status(i), last_attempt_at=i.last_attempt_at) for i in rows]
    else:
        # Before disbursement the schedule is the proposal's fixed terms; nothing is owed yet.
        schedule = [{**item, 'paid_minor': 0, 'status': 'proposed', 'last_attempt_at': None} for item in loan.schedule]
    borrower = db.get(User, loan.borrower_id)
    data.update(borrower_name=borrower.full_name, schedule=schedule, eligible_count=len(loan.eligible_voters), support_count=supports, reject_count=len(votes) - supports,
                vote_count=len(votes), my_vote=next((v.choice for v in votes if v.user_id == user.id), None), can_vote=user.id in loan.eligible_voters,
                paid_minor=paid, outstanding_minor=loan.total_minor - paid,
                quorum_required=(len(loan.eligible_voters) * loan.quorum_bps + 9999) // 10000)
    return data


def instalment_status(item: Instalment) -> str:
    if item.paid_minor >= item.amount_minor:
        return 'paid'
    due = aware(item.due_at)
    if due < now():
        return 'overdue'
    if item.status == 'failed':
        return 'failed'
    if item.paid_minor:
        return 'partially_paid'
    return 'due' if due <= now() + timedelta(days=3) else 'upcoming'


def create_instalments(db: Session, loan: Loan):
    for item in loan.schedule:
        db.add(Instalment(loan_id=loan.id, number=item['number'], amount_minor=item['amount_minor'], due_at=datetime.fromisoformat(item['due_at'])))
    db.flush()


def finalize_loan(db: Session, loan: Loan, actor_id: str | None):
    if loan.status != 'pending_vote':
        return
    votes = db.scalars(select(Vote).where(Vote.loan_id == loan.id)).all()
    if aware(loan.deadline) > now() and len(votes) < len(loan.eligible_voters):
        fail(409, 'Voting remains open until the deadline or all eligible voters have voted.')
    quorum = len(votes) * 10000 >= len(loan.eligible_voters) * loan.quorum_bps
    support = sum(v.choice == 'support' for v in votes)
    passed = bool(votes) and quorum and support * 10000 >= len(votes) * loan.threshold_bps
    target = 'rejected' if not passed else ('approved_awaiting_acceptance' if balances(db, loan.circle_id)['available_minor'] >= loan.principal_minor else 'approved_insufficient_funds')
    transition(db, loan, target, actor_id)
    notify_circle(db, loan.circle_id, 'Voting result is in', f'{loan.purpose}: {target.replace("_", " ")}.')
    if passed:
        notify(db, loan.borrower_id, 'Review and accept your loan terms', 'Your circle approved a proposal. Acceptance is required before a simulated disbursement.', loan.circle_id)


def get_loan(db: Session, loan_id: str, user: User, admin=False, lock=False):
    loan = db.get(Loan, loan_id)
    if not loan:
        fail(404, 'Loan not found.')
    member(db, loan.circle_id, user, admin=admin, lock=lock)
    if lock:
        db.refresh(loan)
    return loan


@router.get('/circles/{circle_id}/transactions')
def transactions(circle_id: str, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user)
    stmt = select(Journal).where(Journal.circle_id == circle_id)
    rows = db.scalars(stmt.order_by(Journal.created_at.desc(), Journal.id).offset((page - 1) * limit).limit(limit))
    return {'items': [journal_view(r, db) for r in rows], 'total': db.scalar(select(func.count()).select_from(stmt.subquery())), 'page': page, 'limit': limit}


@router.post('/circles/{circle_id}/contributions', status_code=201)
def contribute(circle_id: str, data: s.Contribution, idempotency_key: str = Header(), user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, lock=True)
    if data.goal_id:
        goal = db.get(Goal, data.goal_id)
        if not goal or goal.circle_id != circle_id or user.id not in goal.eligible_user_ids:
            fail(403, 'You are not eligible to contribute to this goal.')
        if goal.status != 'active' or aware(goal.start_date) > now() or aware(goal.target_date) < now():
            fail(409, 'This goal is not accepting contributions.')
    row = post_journal(db, circle_id, user.id, idempotency_key, 'contribution', data.amount_minor, [('cash', data.amount_minor), ('external_simulation', -data.amount_minor)], goal_id=data.goal_id)
    # Deduplicate notifications with the immutable journal's audit record.
    if not db.scalar(select(NotificationExists.id).where(NotificationExists.entity_id == row.id, NotificationExists.action == 'contribution.notified')):
        notify(db, user.id, 'Simulated contribution recorded', 'SIMULATED — no real money was transferred.', circle_id)
        audit(db, user.id, 'contribution.notified', row.id, circle_id)
    return journal_view(row, db)


# Alias keeps the notification dedup query expressive without another table.
NotificationExists = Audit


@router.post('/circles/{circle_id}/transactions/{journal_id}/reverse')
def reverse(circle_id: str, journal_id: str, idempotency_key: str = Header(), user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, admin=True, lock=True)
    row = db.get(Journal, journal_id)
    if not row or row.circle_id != circle_id or row.kind != 'contribution':
        fail(404, 'Contribution not found.')
    previous = db.scalar(select(Journal).where(Journal.reversal_of == row.id))
    if previous:
        if previous.idempotency_key != idempotency_key:
            fail(409, 'This contribution was already reversed.')
        return journal_view(previous, db)
    if balances(db, circle_id)['available_minor'] < row.amount_minor:
        fail(409, 'Available funds are insufficient to reverse this contribution.')
    reversal = post_journal(db, circle_id, user.id, idempotency_key, 'contribution_reversal', row.amount_minor, [('cash', -row.amount_minor), ('external_simulation', row.amount_minor)], goal_id=row.goal_id, reversal_of=row.id)
    notify(db, row.user_id, 'Simulated contribution reversed', 'A compensating entry was added to the ledger.', circle_id)
    return journal_view(reversal, db)


@router.get('/circles/{circle_id}/loan-requests')
def loan_requests(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user)
    rows = db.execute(select(LoanRequest, User.full_name).join(User, User.id == LoanRequest.borrower_id).where(LoanRequest.circle_id == circle_id).order_by(LoanRequest.created_at.desc()).limit(100))
    return [dict(id=r.id, borrower_id=r.borrower_id, borrower_name=name, principal_minor=r.principal_minor, purpose=r.purpose, explanation=r.explanation, frequency=r.frequency, instalment_count=r.instalment_count, status=r.status) for r, name in rows]


@router.post('/circles/{circle_id}/loan-requests', status_code=201)
def request_loan(circle_id: str, data: s.RequestInput, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, lock=True)
    pending = db.scalar(select(func.count()).select_from(LoanRequest).where(LoanRequest.circle_id == circle_id, LoanRequest.borrower_id == user.id, LoanRequest.status == 'requested'))
    if pending >= 3:
        fail(409, 'You already have three requests awaiting review.')
    row = LoanRequest(circle_id=circle_id, borrower_id=user.id, **data.model_dump())
    db.add(row)
    db.flush()
    audit(db, user.id, 'loan.requested', row.id, circle_id)
    for m in active_members(db, circle_id):
        if m.role in ('owner', 'admin'):
            notify(db, m.user_id, 'New loan request', f'{user.full_name} requested a loan for {row.purpose}.', circle_id)
    return {'id': row.id}


@router.get('/circles/{circle_id}/loans')
def loans(circle_id: str, page: int = Query(1, ge=1), limit: int = Query(12, ge=1, le=50), user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user)
    stmt = select(Loan).where(Loan.circle_id == circle_id)
    return {'items': [loan_view(db, loan, user) for loan in db.scalars(stmt.order_by(Loan.created_at.desc()).offset((page - 1) * limit).limit(limit))], 'page': page, 'limit': limit, 'total': db.scalar(select(func.count()).select_from(stmt.subquery()))}


@router.post('/circles/{circle_id}/proposals', status_code=201)
def proposal(circle_id: str, data: s.ProposalInput, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, admin=True, lock=True)
    loan = create_proposal(db, circle_id, user, data)
    return loan_view(db, loan, user)


@router.get('/loans')
def my_loans(page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=50), user: User = Depends(session_user), db: Session = Depends(get_db)):
    from .models import Membership
    ids = select(Membership.circle_id).where(Membership.user_id == user.id, Membership.status == 'active')
    stmt = select(Loan).where(Loan.circle_id.in_(ids))
    rows = db.scalars(stmt.order_by(Loan.created_at.desc(), Loan.id).offset((page - 1) * limit).limit(limit))
    return {'items': [loan_view(db, loan, user) for loan in rows], 'page': page, 'limit': limit, 'total': db.scalar(select(func.count()).select_from(stmt.subquery()))}


@router.get('/loans/{loan_id}')
def detail(loan_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    return loan_view(db, get_loan(db, loan_id, user), user)


@router.post('/loans/{loan_id}/votes')
def vote(loan_id: str, data: s.VoteInput, user: User = Depends(session_user), db: Session = Depends(get_db)):
    loan = get_loan(db, loan_id, user, lock=True)
    if loan.status != 'pending_vote' or aware(loan.deadline) <= now():
        fail(409, 'Voting is closed.')
    if user.id not in loan.eligible_voters or user.id == loan.borrower_id:
        fail(403, 'You are not an eligible voter for this proposal version.')
    if db.scalar(select(Vote).where(Vote.loan_id == loan.id, Vote.user_id == user.id)):
        fail(409, 'Your vote has already been recorded. Votes cannot be changed.')
    db.add(Vote(loan_id=loan.id, user_id=user.id, choice=data.choice))
    audit(db, user.id, 'vote.recorded', loan.id, loan.circle_id, choice=data.choice, version=loan.version)
    db.flush()
    return loan_view(db, loan, user)


@router.post('/loans/{loan_id}/finalize')
def finalize(loan_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    loan = get_loan(db, loan_id, user, lock=True)
    finalize_loan(db, loan, user.id)
    return loan_view(db, loan, user)


@router.post('/loans/{loan_id}/accept')
def accept(loan_id: str, data: s.AcceptInput, idempotency_key: str = Header(), user: User = Depends(session_user), db: Session = Depends(get_db)):
    loan = get_loan(db, loan_id, user, lock=True)
    key_check(idempotency_key)
    if loan.borrower_id != user.id:
        fail(403, 'Only the borrower may accept these terms.')
    fp = fingerprint(action='accept', loan_id=loan.id, user_id=user.id, **data.model_dump())
    op = db.scalar(select(Operation).where(Operation.circle_id == loan.circle_id, Operation.key == idempotency_key))
    if op:
        if op.fingerprint != fp:
            fail(409, 'Idempotency key conflict.')
        return loan_view(db, loan, user)
    if data.version != loan.version or data.terms_hash != loan.terms_hash:
        fail(409, 'These are not the current terms. Review the proposal again.')
    if loan.status not in ('approved_awaiting_acceptance', 'approved_insufficient_funds', 'disbursement_failed'):
        fail(409, 'This proposal is not awaiting acceptance.')
    if aware(loan.acceptance_expires_at) <= now():
        transition(db, loan, 'expired', user.id)
        db.commit()
        fail(409, 'This proposal has expired.')
    if not loan.accepted_at:
        loan.accepted_at, loan.accepted_version = now(), loan.version
    transition(db, loan, 'accepted_pending_checks', user.id)
    # Email verified membership is required. No claim of independent KYC; simulation only.
    target = 'disbursement_pending' if balances(db, loan.circle_id)['available_minor'] >= loan.principal_minor else 'approved_insufficient_funds'
    transition(db, loan, target, user.id)
    db.add(Operation(circle_id=loan.circle_id, user_id=user.id, key=idempotency_key, fingerprint=fp, entity_id=loan.id))
    notify_circle(db, loan.circle_id, 'Borrower accepted the terms', f'{loan.purpose}: {target.replace("_", " ")}. No real money moved.')
    return loan_view(db, loan, user)


@router.post('/loans/{loan_id}/disburse')
def disburse(loan_id: str, idempotency_key: str = Header(), user: User = Depends(session_user), db: Session = Depends(get_db)):
    loan = get_loan(db, loan_id, user, admin=True, lock=True)
    key_check(idempotency_key)
    previous = db.scalar(select(Journal).where(Journal.loan_id == loan.id, Journal.kind == 'disbursement'))
    if previous:
        if previous.idempotency_key != idempotency_key or previous.user_id != user.id:
            fail(409, 'This loan already has a simulated disbursement.')
        return loan_view(db, loan, user)
    if loan.status != 'disbursement_pending' or loan.accepted_version != loan.version:
        fail(409, 'Borrower acceptance and successful checks are required.')
    if aware(loan.acceptance_expires_at) <= now():
        transition(db, loan, 'expired', user.id)
        db.commit()
        fail(409, 'Disbursement window expired. Reserved funds have been released.')
    if balances(db, loan.circle_id)['cash_minor'] < loan.principal_minor:
        fail(409, 'The circle has insufficient funds.')
    post_journal(db, loan.circle_id, user.id, idempotency_key, 'disbursement', loan.principal_minor, [('cash', -loan.principal_minor), ('loan_receivable', loan.principal_minor)], loan_id=loan.id)
    transition(db, loan, 'disbursed', user.id)
    create_instalments(db, loan)
    notify(db, loan.borrower_id, 'Your repayment schedule is ready', f'{len(loan.schedule)} {loan.frequency} instalments are now scheduled. Simulation only.', loan.circle_id)
    notify_circle(db, loan.circle_id, 'Simulated disbursement recorded', 'SIMULATED — no real money was transferred.')
    return loan_view(db, loan, user)


@router.post('/loans/{loan_id}/repayments')
def repay(loan_id: str, data: s.Repayment, idempotency_key: str = Header(), user: User = Depends(session_user), db: Session = Depends(get_db)):
    loan = get_loan(db, loan_id, user, lock=True)
    if loan.borrower_id != user.id:
        fail(403, 'Only the borrower may record a simulated repayment.')
    key_check(idempotency_key)
    fp = fingerprint(action='repay', loan_id=loan.id, user_id=user.id, **data.model_dump())
    existing = db.scalar(select(RepaymentAttempt).where(RepaymentAttempt.loan_id == loan.id, RepaymentAttempt.idempotency_key == idempotency_key))
    if existing:
        if existing.fingerprint != fp:
            fail(409, 'Idempotency key conflict.')
        return loan_view(db, loan, user)
    if loan.status not in ('disbursed', 'repaying'):
        fail(409, 'This loan is not open for repayments.')
    paid = loan_view(db, loan, user)['paid_minor']
    if data.amount_minor > loan.total_minor - paid:
        fail(422, 'The repayment exceeds the outstanding amount.')
    open_items = [i for i in db.scalars(select(Instalment).where(Instalment.loan_id == loan.id).order_by(Instalment.number)) if i.paid_minor < i.amount_minor]
    if data.outcome == 'failure':
        # Simulated collection failure: no ledger entry, a private notice to the borrower, follow-up for admins only.
        attempt = RepaymentAttempt(loan_id=loan.id, user_id=user.id, amount_minor=data.amount_minor, status='failed', reason='Simulated collection failure. No money moved.',
                                   idempotency_key=idempotency_key, fingerprint=fp)
        db.add(attempt)
        if open_items:
            open_items[0].status, open_items[0].last_attempt_at = 'failed', now()
        db.flush()
        audit(db, user.id, 'repayment.failed', attempt.id, loan.circle_id, amount_minor=data.amount_minor, provenance='simulation')
        notify(db, user.id, 'Repayment did not go through', 'A simulated repayment attempt failed. Nothing was deducted. You can try again or talk to your circle admins.', loan.circle_id)
        for m in active_members(db, loan.circle_id):
            if m.role in ('owner', 'admin') and m.user_id != user.id:
                notify(db, m.user_id, 'Repayment follow-up needed', f'A repayment attempt for "{loan.purpose}" did not complete. Please follow up privately.', loan.circle_id)
        return loan_view(db, loan, user)
    principal_part = min(data.amount_minor, max(0, loan.principal_minor - paid))
    income_part = data.amount_minor - principal_part
    journal = post_journal(db, loan.circle_id, user.id, idempotency_key, 'repayment', data.amount_minor,
                           [('cash', data.amount_minor), ('loan_receivable', -principal_part), ('finance_income', -income_part)], loan_id=loan.id)
    remaining = data.amount_minor
    for item in open_items:
        if not remaining:
            break
        part = min(remaining, item.amount_minor - item.paid_minor)
        item.paid_minor += part
        item.status = 'paid' if item.paid_minor == item.amount_minor else 'partially_paid'
        item.last_attempt_at = now()
        item.payment_refs = [*item.payment_refs, journal.id]
        remaining -= part
    db.add(RepaymentAttempt(loan_id=loan.id, user_id=user.id, amount_minor=data.amount_minor, status='succeeded', journal_id=journal.id,
                            idempotency_key=idempotency_key, fingerprint=fp))
    transition(db, loan, 'repaid' if paid + data.amount_minor == loan.total_minor else 'repaying', user.id)
    notify(db, user.id, 'Simulated repayment recorded', 'SIMULATED — no real money was transferred.', loan.circle_id)
    return loan_view(db, loan, user)


@router.get('/loans/{loan_id}/repayment-attempts')
def repayment_attempts(loan_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    loan = get_loan(db, loan_id, user)
    _, own = member(db, loan.circle_id, user)
    # Repayment problems are private to the borrower and authorised circle administrators.
    if user.id != loan.borrower_id and own.role not in ('owner', 'admin'):
        fail(403, 'Only the borrower and circle administrators can view repayment attempts.')
    rows = db.scalars(select(RepaymentAttempt).where(RepaymentAttempt.loan_id == loan.id).order_by(RepaymentAttempt.created_at.desc()).limit(100))
    return [dict(id=r.id, amount_minor=r.amount_minor, currency=r.currency, status=r.status, provenance=r.provenance, reason=r.reason, created_at=r.created_at) for r in rows]


@router.post('/loans/{loan_id}/cancel')
def cancel(loan_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    loan = get_loan(db, loan_id, user, lock=True)
    _, own = member(db, loan.circle_id, user)
    if user.id != loan.borrower_id and own.role not in ('owner', 'admin'):
        fail(403, 'Only the borrower or circle administrators may close a proposal.')
    transition(db, loan, 'cancelled', user.id)
    notify_circle(db, loan.circle_id, 'Proposal closed', f'Version {loan.version} was cancelled. Its votes and terms remain in the audit history.')
    return loan_view(db, loan, user)


@router.get('/circles/{circle_id}/reconciliation')
def reconcile(circle_id: str, user: User = Depends(session_user), db: Session = Depends(get_db)):
    member(db, circle_id, user, admin=True)
    groups = db.execute(select(Entry.journal_id, func.sum(Entry.amount_minor), func.count()).where(Entry.circle_id == circle_id).group_by(Entry.journal_id)).all()
    count = db.scalar(select(func.count()).select_from(Journal).where(Journal.circle_id == circle_id))
    invalid = [g[0] for g in groups if g[1] != 0 or g[2] < 2]
    # Instalment allocations must equal the authoritative repayment journals for each loan.
    mismatched = []
    for loan in db.scalars(select(Loan).where(Loan.circle_id == circle_id, Loan.status.in_(['disbursed', 'repaying', 'repaid']))):
        journals = db.scalar(select(func.coalesce(func.sum(Journal.amount_minor), 0)).where(Journal.loan_id == loan.id, Journal.kind == 'repayment'))
        allocated = db.scalar(select(func.coalesce(func.sum(Instalment.paid_minor), 0)).where(Instalment.loan_id == loan.id))
        if journals != allocated:
            mismatched.append(loan.id)
    return {'balanced': not invalid and len(groups) == count and not mismatched, 'journal_count': count, 'unbalanced_journal_ids': invalid,
            'instalment_mismatch_loan_ids': mismatched, **balances(db, circle_id)}
