"""Transparent community participation levels. Not a credit score and never used for loan eligibility.

Points come only from recorded, explainable events. Simulated money is listed as practice and earns no points,
because it is not real financial activity. Having no history, or a rejected request, never lowers a level.
Private messages and personal characteristics are never used.
"""
from dataclasses import dataclass

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Audit, Instalment, Journal, Loan, Membership, User, Vote
from .security import session_user

router = APIRouter(prefix='/api/v1')

# Configurable rules: event -> points per occurrence, with a cap so one behaviour cannot dominate.
RULES = {
    'circle_joined': {'points': 10, 'cap': 5, 'label': 'Joined a circle'},
    'vote_cast': {'points': 5, 'cap': 20, 'label': 'Took part in a circle vote'},
    'goal_created': {'points': 5, 'cap': 5, 'label': 'Set up a shared savings goal'},
    'confirmed_contribution': {'points': 15, 'cap': 20, 'label': 'Provider-confirmed contribution'},
    'instalment_on_time': {'points': 20, 'cap': 24, 'label': 'Provider-confirmed instalment paid on time'},
}
LEVELS = [(0, 'Starter'), (40, 'Builder'), (120, 'Trusted'), (250, 'Circle Champion')]


@dataclass
class Event:
    kind: str
    count: int


def level_for(points: int) -> tuple[str, str | None, int | None]:
    current, nxt, threshold = LEVELS[0][1], None, None
    for index, (minimum, name) in enumerate(LEVELS):
        if points >= minimum:
            current = name
            nxt, threshold = (LEVELS[index + 1][1], LEVELS[index + 1][0]) if index + 1 < len(LEVELS) else (None, None)
    return current, nxt, threshold


def score(events: list[Event]) -> dict:
    breakdown, total = [], 0
    for event in events:
        rule = RULES[event.kind]
        counted = min(event.count, rule['cap'])
        points = counted * rule['points']
        total += points
        breakdown.append({'event': event.kind, 'label': rule['label'], 'occurrences': event.count, 'counted': counted, 'points': points})
    level, next_level, next_at = level_for(total)
    return {'points': total, 'level': level, 'next_level': next_level, 'next_level_at': next_at, 'breakdown': breakdown}


def collect(db: Session, user: User) -> tuple[list[Event], dict]:
    joined = db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == user.id, Membership.status == 'active'))
    votes = db.scalar(select(func.count()).select_from(Vote).where(Vote.user_id == user.id))
    goals = db.scalar(select(func.count()).select_from(Audit).where(Audit.actor_id == user.id, Audit.action == 'goal.created'))
    real = db.scalar(select(func.count()).select_from(Journal).where(Journal.user_id == user.id, Journal.kind == 'contribution', Journal.provenance != 'simulation'))
    on_time = db.scalar(select(func.count()).select_from(Instalment).join(Loan, Loan.id == Instalment.loan_id).where(
        Loan.borrower_id == user.id, Loan.provenance != 'simulation', Instalment.status == 'paid', Instalment.last_attempt_at <= Instalment.due_at))
    practice = {
        'simulated_contributions': db.scalar(select(func.count()).select_from(Journal).where(Journal.user_id == user.id, Journal.kind == 'contribution', Journal.provenance == 'simulation')),
        'simulated_repayments': db.scalar(select(func.count()).select_from(Journal).where(Journal.user_id == user.id, Journal.kind == 'repayment', Journal.provenance == 'simulation')),
    }
    return [Event('circle_joined', joined), Event('vote_cast', votes), Event('goal_created', goals), Event('confirmed_contribution', real), Event('instalment_on_time', on_time)], practice


@router.get('/me/progress')
def progress(user: User = Depends(session_user), db: Session = Depends(get_db)):
    events, practice = collect(db, user)
    return {**score(events), 'practice': practice, 'levels': [{'name': n, 'from_points': p} for p, n in LEVELS],
            'notice': 'Participation levels reflect recorded community activity. They are not a credit score and do not affect loan eligibility. Simulated money earns no points.'}
