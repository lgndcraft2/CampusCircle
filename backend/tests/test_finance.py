from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.community import balances
from app.finance import calculate_terms, post_journal
from app.models import Entry, Journal, Loan, now


def fund(client, cid, amount=100000, key='funding-key-0001'):
    r = client.post(f'/api/v1/circles/{cid}/contributions', json={'amount_minor': amount}, headers={'Idempotency-Key': key})
    assert r.status_code == 201, r.text
    return r


def approve(clients, loan):
    for i in (0, 2):
        assert clients[i].post(f'/api/v1/loans/{loan["id"]}/votes', json={'choice': 'support'}).status_code == 200
    r = clients[0].post(f'/api/v1/loans/{loan["id"]}/finalize')
    assert r.status_code == 200, r.text
    return r.json()


def test_rounding_is_exact_and_calendar_safe():
    interest, total, schedule = calculate_terms(10001, 500, 31, 3, 'monthly', now().replace(month=1, day=31))
    assert interest == 500
    assert total == 10532
    assert sum(s['amount_minor'] for s in schedule) == total
    assert [s['amount_minor'] for s in schedule] == [3511, 3511, 3510]
    assert '-02-' in schedule[0]['due_at']


def test_contribution_idempotency_balancing_and_conflict(store, clients):
    factory, _, cid = store
    first = fund(clients[0], cid)
    second = fund(clients[0], cid)
    assert first.json()['id'] == second.json()['id']
    r = clients[0].post(f'/api/v1/circles/{cid}/contributions', json={'amount_minor': 99}, headers={'Idempotency-Key': 'funding-key-0001'})
    assert r.status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Journal)) == 1
        assert db.scalar(select(func.sum(Entry.amount_minor))) == 0
        assert balances(db, cid)['available_minor'] == 100000
    assert first.json()['provenance'] == 'simulation'


def test_permission_boundaries_and_float_rejection(store, clients):
    cid = store[2]
    assert clients[3].get(f'/api/v1/circles/{cid}').status_code == 404
    for path in ('transactions', 'messages', 'loans', 'goals', 'members', 'activity'):
        assert clients[3].get(f'/api/v1/circles/{cid}/{path}').status_code == 404
    assert clients[1].post(f'/api/v1/circles/{cid}/invitations').status_code == 403
    assert clients[0].post(f'/api/v1/circles/{cid}/contributions', json={'amount_minor': 12.5}, headers={'Idempotency-Key': 'float-test-0001'}).status_code == 422


def test_vote_eligibility_duplicate_and_early_finalization(store, clients, proposal):
    lid = proposal['id']
    assert clients[1].post(f'/api/v1/loans/{lid}/votes', json={'choice': 'support'}).status_code == 403
    assert clients[3].post(f'/api/v1/loans/{lid}/votes', json={'choice': 'support'}).status_code == 404
    assert clients[0].post(f'/api/v1/loans/{lid}/finalize').status_code == 409
    assert clients[0].post(f'/api/v1/loans/{lid}/votes', json={'choice': 'support'}).status_code == 200
    assert clients[0].post(f'/api/v1/loans/{lid}/votes', json={'choice': 'reject'}).status_code == 409


def test_full_loan_lifecycle_and_reservation(store, clients, proposal):
    factory, _, cid = store
    fund(clients[0], cid)
    approved = approve(clients, proposal)
    assert approved['status'] == 'approved_awaiting_acceptance'
    lid = approved['id']
    assert clients[0].post(f'/api/v1/loans/{lid}/disburse', headers={'Idempotency-Key': 'premature-disburse'}).status_code == 409
    payload = {'version': approved['version'], 'terms_hash': approved['terms_hash']}
    accepted = clients[1].post(f'/api/v1/loans/{lid}/accept', json=payload, headers={'Idempotency-Key': 'borrower-accept-01'})
    assert accepted.json()['status'] == 'disbursement_pending'
    with factory() as db:
        assert balances(db, cid)['reserved_minor'] == 10001
        assert balances(db, cid)['available_minor'] == 89999
    assert clients[1].post(f'/api/v1/loans/{lid}/accept', json=payload, headers={'Idempotency-Key': 'borrower-accept-01'}).status_code == 200
    for _ in range(2):
        r = clients[0].post(f'/api/v1/loans/{lid}/disburse', headers={'Idempotency-Key': 'disbursement-0001'})
        assert r.json()['status'] == 'disbursed', r.text
    for amount, key in [(3000, 'repayment-0001'), (7532, 'repayment-0002')]:
        for _ in range(2):
            r = clients[1].post(f'/api/v1/loans/{lid}/repayments', json={'amount_minor': amount}, headers={'Idempotency-Key': key})
            assert r.status_code == 200, r.text
    assert r.json()['status'] == 'repaid'
    assert r.json()['outstanding_minor'] == 0
    assert all(i['paid_minor'] == i['amount_minor'] for i in r.json()['schedule'])
    with factory() as db:
        assert balances(db, cid)['cash_minor'] == 100531
        assert db.scalar(select(func.sum(Entry.amount_minor)).where(Entry.account == 'loan_receivable')) == 0


def test_insufficient_funds_and_wrong_terms(store, clients, proposal):
    approved = approve(clients, proposal)
    assert approved['status'] == 'approved_insufficient_funds'
    lid = approved['id']
    assert clients[0].post(f'/api/v1/loans/{lid}/accept', json={'version': 1, 'terms_hash': approved['terms_hash']}, headers={'Idempotency-Key': 'wrong-borrower-01'}).status_code == 403
    assert clients[1].post(f'/api/v1/loans/{lid}/accept', json={'version': 2, 'terms_hash': approved['terms_hash']}, headers={'Idempotency-Key': 'wrong-version-01'}).status_code == 409
    r = clients[1].post(f'/api/v1/loans/{lid}/accept', json={'version': 1, 'terms_hash': approved['terms_hash']}, headers={'Idempotency-Key': 'insufficient-001'})
    assert r.json()['status'] == 'approved_insufficient_funds'
    assert r.json()['accepted_version'] == 1
    with store[0]() as db:
        assert db.scalar(select(func.count()).select_from(Journal)) == 0


def test_tie_rejects_and_terms_are_immutable(store, clients, proposal):
    lid = proposal['id']
    clients[0].post(f'/api/v1/loans/{lid}/votes', json={'choice': 'support'})
    clients[2].post(f'/api/v1/loans/{lid}/votes', json={'choice': 'reject'})
    assert clients[0].post(f'/api/v1/loans/{lid}/finalize').json()['status'] == 'rejected'
    with store[0]() as db:
        loan = db.get(Loan, lid)
        loan.principal_minor = 1
        with pytest.raises(ValueError, match='immutable'):
            db.flush()


def test_concurrent_contributions_same_key(store, clients):
    factory, ids, cid = store
    def write():
        with factory.begin() as db:
            # Same lock convention as the API, with SQLite BEGIN IMMEDIATE.
            db.get(Loan, 'not-present')
            row = post_journal(db, cid, ids[0], 'concurrent-key-01', 'contribution', 500, [('cash', 500), ('external_simulation', -500)])
            return row.id
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids_created = list(pool.map(lambda _: write(), range(4)))
    assert len(set(ids_created)) == 1
    with factory() as db:
        assert balances(db, cid)['cash_minor'] == 500


def test_reversal_compensates_without_changing_original(store, clients):
    cid = store[2]
    original = fund(clients[0], cid).json()
    r = clients[0].post(f'/api/v1/circles/{cid}/transactions/{original["id"]}/reverse', headers={'Idempotency-Key': 'reverse-key-0001'})
    assert r.status_code == 200, r.text
    assert clients[0].get(f'/api/v1/circles/{cid}').json()['cash_minor'] == 0
    assert clients[0].get(f'/api/v1/circles/{cid}/reconciliation').json()['balanced']
    with store[0]() as db:
        assert db.get(Journal, original['id']).status == 'simulated_confirmed'
