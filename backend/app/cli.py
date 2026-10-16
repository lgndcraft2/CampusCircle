"""Development tools. Synthetic demo data is created through the real HTTP API so every business rule applies."""
import argparse
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from .config import ROOT, settings
from .db import SessionLocal, engine
from .models import User, now
from .security import hasher

DEMO_PASSWORD = 'CampusCircle-demo-2026!'
STUDENTS = [('Amina Yusuf', 'amina', 'Computer Science'), ('Tobi Adeyemi', 'tobi', 'Computer Science'),
            ('Zainab Bello', 'zainab', 'Computer Science'), ('Chidi Okafor', 'chidi', 'Economics')]
CIRCLES = [
    ('The Study Fund', 'School essentials', 'A little from each of us. A lot more possibilities for all of us. Our shared fund for books, tuition gaps, and the unexpected.', 'A stronger semester', 500_000_00),
    ('Creators on Campus', 'Creative projects', 'Big ideas deserve a small beginning. Saving for our next campus exhibition, equipment, and creative projects.', 'Our first exhibition', 300_000_00),
    ('Next Semester', 'Tuition & fees', 'Making next semester a little lighter, one contribution at a time. A student-led circle for planning ahead.', 'Semester essentials', 750_000_00),
]
RULES = 'Contributions are pooled and not individually withdrawable. Loans require a member vote and borrower acceptance. All financial activity is simulated.'


def ok(response, expected=(200, 201)):
    if response.status_code not in expected:
        raise RuntimeError(f'Seed step failed: {response.request.method} {response.request.url.path} -> {response.status_code} {response.text[:300]}')
    return response.json()


def seed():
    if settings().environment == 'production':
        raise RuntimeError('Synthetic demo seeding is disabled in production.')
    from fastapi.testclient import TestClient
    from .main import app
    with SessionLocal.begin() as db:
        if db.scalar(select(User).where(User.email == 'amina@students.example.edu')):
            print('Synthetic demo already exists; no records changed. Use `reset` to start over.')
            return
        encoded = hasher.hash(DEMO_PASSWORD)
        for name, handle, dept in STUDENTS:
            # Synthetic people with reserved example.edu addresses. Email verification is pre-set for the demo only.
            db.add(User(full_name=name, email=f'{handle}@students.example.edu', phone=f'+23480100000{STUDENTS.index((name, handle, dept)):02d}',
                        university='Demo University', department=dept, password_hash=encoded, email_verified=True))
    clients = {}
    for _, handle, _ in STUDENTS:
        c = TestClient(app)
        ok(c.post('/api/v1/auth/login', json={'email': f'{handle}@students.example.edu', 'password': DEMO_PASSWORD}))
        c.headers['x-csrf-token'] = c.cookies['cc_csrf']
        clients[handle] = c
    amina, tobi, zainab = clients['amina'], clients['tobi'], clients['zainab']
    key = iter(range(1000))
    idem = lambda: {'Idempotency-Key': f'seed-operation-{next(key):04d}'}  # noqa: E731
    target_date = (now() + timedelta(days=90)).isoformat()
    for index, (name, purpose, description, goal, target) in enumerate(CIRCLES):
        circle = ok(amina.post('/api/v1/circles', json={'name': name, 'description': description, 'purpose': purpose, 'university': 'Demo University',
                                                          'joining_method': 'open', 'rules': RULES, 'membership_limit': 20}))
        cid = circle['id']
        for member in (tobi, zainab):
            ok(member.post(f'/api/v1/circles/{cid}/join'))
        ok(amina.post(f'/api/v1/circles/{cid}/goals', json={'name': goal, 'description': 'Our shared savings goal. Every recorded contribution counts.', 'target_minor': target,
                                                             'start_date': (now() - timedelta(minutes=1)).isoformat(), 'target_date': target_date}))
        goal_id = ok(amina.get(f'/api/v1/circles/{cid}/goals'))[0]['id']
        for member, amount in ((amina, 45_000_00), (tobi, 25_000_00), (zainab, 15_000_00)):
            ok(member.post(f'/api/v1/circles/{cid}/contributions', json={'amount_minor': amount * (index + 1), 'goal_id': goal_id}, headers=idem()))
        ok(zainab.post(f'/api/v1/circles/{cid}/messages', json={'body': 'Welcome to our circle! Let’s agree on a contribution amount that works for everyone.'}))
        ok(tobi.post(f'/api/v1/circles/{cid}/messages', json={'body': 'Love this. Small steps, together. I’ve added my simulated contribution.'}))
        if index == 0:
            # An open proposal awaiting votes.
            req = ok(tobi.post(f'/api/v1/circles/{cid}/loan-requests', json={'principal_minor': 25_000_00, 'purpose': 'A laptop for final-year project',
                                                                             'explanation': 'A synthetic demo request for laptop repair and project work.', 'frequency': 'monthly', 'instalment_count': 3}))
            # Round figures the demo can quote: N25,000 + 2% (N500) + N300 fee = N25,800, three instalments of N8,600.
            # A 7-day window keeps the vote open between seeding and recording.
            loan = ok(amina.post(f'/api/v1/circles/{cid}/proposals', json={'request_id': req['id'], 'interest_bps': 200, 'fees_minor': 300_00, 'instalment_count': 3,
                                                                           'voting_hours': 168, 'conditions': 'The ₦300 fee covers laptop repair receipts being shared with the circle. If an instalment is late, the borrower tells the circle within 7 days.'}))
            ok(zainab.post(f'/api/v1/loans/{loan["id"]}/votes', json={'choice': 'support'}))
        if index == 1:
            # A completed vote, acceptance, simulated disbursement, one repayment, and one failed attempt.
            req = ok(zainab.post(f'/api/v1/circles/{cid}/loan-requests', json={'principal_minor': 30_000_00, 'purpose': 'Camera lens for the exhibition',
                                                                               'explanation': 'A synthetic demo request to rent a lens for the exhibition shoot.', 'frequency': 'monthly', 'instalment_count': 3}))
            loan = ok(amina.post(f'/api/v1/circles/{cid}/proposals', json={'request_id': req['id'], 'interest_bps': 300, 'fees_minor': 500_00, 'instalment_count': 3,
                                                                           'voting_hours': 24, 'conditions': 'If an instalment is late, the borrower talks to the circle within 7 days.'}))
            for voter in (amina, tobi):
                ok(voter.post(f'/api/v1/loans/{loan["id"]}/votes', json={'choice': 'support'}))
            loan = ok(amina.post(f'/api/v1/loans/{loan["id"]}/finalize'))
            loan = ok(zainab.post(f'/api/v1/loans/{loan["id"]}/accept', json={'version': loan['version'], 'terms_hash': loan['terms_hash']}, headers=idem()))
            loan = ok(amina.post(f'/api/v1/loans/{loan["id"]}/disburse', headers=idem()))
            first = loan['schedule'][0]['amount_minor']
            ok(zainab.post(f'/api/v1/loans/{loan["id"]}/repayments', json={'amount_minor': first}, headers=idem()))
            ok(zainab.post(f'/api/v1/loans/{loan["id"]}/repayments', json={'amount_minor': loan['schedule'][1]['amount_minor'], 'outcome': 'failure'}, headers=idem()))
    for c in clients.values():
        c.close()
    print('Created synthetic demo through the API: 4 students, 3 circles, goals, simulated contributions, chat, an open proposal, '
          'and a disbursed loan with one repayment and one failed attempt. Password for every demo account: ' + DEMO_PASSWORD)


def reset():
    cfg = settings()
    if cfg.environment == 'production':
        raise RuntimeError('Reset is disabled in production.')
    from alembic import command
    from alembic.config import Config
    from .db import Base
    engine.dispose()
    if cfg.database_url.startswith('sqlite'):
        path = Path(cfg.database_url.removeprefix('sqlite:///'))
        for suffix in ('', '-wal', '-shm'):
            Path(f'{path}{suffix}').unlink(missing_ok=True)
    else:
        with engine.begin() as conn:
            Base.metadata.drop_all(conn)
            conn.exec_driver_sql('DROP TABLE IF EXISTS alembic_version')
            for fn in ('cc_append_only', 'cc_loan_terms', 'cc_balanced_journal'):
                conn.exec_driver_sql(f'DROP FUNCTION IF EXISTS {fn}() CASCADE')
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini')), 'head')
    print('Database recreated from migrations.')
    seed()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CampusCircle development tools (no public development endpoints).')
    parser.add_argument('command', choices=['seed', 'reset', 'mail'])
    args = parser.parse_args()
    settings().validate_runtime()
    if args.command == 'seed':
        seed()
    elif args.command == 'reset':
        reset()
    else:
        if settings().environment == 'production' or not settings().development_outbox:
            raise RuntimeError('Development mail reader is disabled.')
        files = sorted((ROOT / '.dev-mail').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for file in files[:10]:
            print(json.dumps(json.loads(file.read_text(encoding='utf-8')), indent=2, ensure_ascii=False))
