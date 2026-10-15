from sqlalchemy import func, select

from app.gamification import Event, score
from app.models import EmailDelivery, Instalment, Journal, Notification, RepaymentAttempt


def disbursed(clients, cid, lid):
    clients[0].post(f'/api/v1/circles/{cid}/contributions', json={'amount_minor': 100000}, headers={'Idempotency-Key': 'fund-for-repay'})
    for i in (0, 2):
        clients[i].post(f'/api/v1/loans/{lid}/votes', json={'choice': 'support'})
    loan = clients[0].post(f'/api/v1/loans/{lid}/finalize').json()
    clients[1].post(f'/api/v1/loans/{lid}/accept', json={'version': loan['version'], 'terms_hash': loan['terms_hash']}, headers={'Idempotency-Key': 'accept-key-001'})
    r = clients[0].post(f'/api/v1/loans/{lid}/disburse', headers={'Idempotency-Key': 'disburse-key-01'})
    assert r.json()['status'] == 'disbursed', r.text
    return r.json()


def test_instalments_created_on_disbursement_only(store, clients, proposal):
    assert all(i['status'] == 'proposed' for i in proposal['schedule'])
    loan = disbursed(clients, store[2], proposal['id'])
    assert [i['amount_minor'] for i in loan['schedule']] == [3511, 3511, 3510]
    assert {i['status'] for i in loan['schedule']} == {'upcoming'}
    with store[0]() as db:
        assert db.scalar(select(func.count()).select_from(Instalment)) == 3


def test_failed_repayment_is_private_and_moves_no_money(store, clients, proposal):
    factory, ids, cid = store
    loan = disbursed(clients, cid, proposal['id'])
    r = clients[1].post(f'/api/v1/loans/{loan["id"]}/repayments', json={'amount_minor': 3511, 'outcome': 'failure'}, headers={'Idempotency-Key': 'failed-repay-01'})
    assert r.status_code == 200
    body = r.json()
    assert body['paid_minor'] == 0 and body['status'] == 'disbursed' and body['schedule'][0]['status'] == 'failed'
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Journal).where(Journal.kind == 'repayment')) == 0
        assert db.scalar(select(RepaymentAttempt)).status == 'failed'
        # Borrower and owner were told; the unrelated member (Student 2) was not.
        titles = {(n.user_id, n.title) for n in db.scalars(select(Notification))}
        assert (ids[1], 'Repayment did not go through') in titles and (ids[0], 'Repayment follow-up needed') in titles
        assert not any(uid == ids[2] and 'Repayment' in t for uid, t in titles)
    assert clients[2].get(f'/api/v1/loans/{loan["id"]}/repayment-attempts').status_code == 403
    assert len(clients[0].get(f'/api/v1/loans/{loan["id"]}/repayment-attempts').json()) == 1
    # A later successful repayment clears the instalment and reconciles with the ledger.
    r = clients[1].post(f'/api/v1/loans/{loan["id"]}/repayments', json={'amount_minor': 5000}, headers={'Idempotency-Key': 'repay-after-fail'})
    statuses = [i['status'] for i in r.json()['schedule']]
    assert statuses[:2] == ['paid', 'partially_paid']
    rec = clients[0].get(f'/api/v1/circles/{cid}/reconciliation').json()
    assert rec['balanced'] and rec['instalment_mismatch_loan_ids'] == []


def test_repayment_idempotency_conflict(store, clients, proposal):
    loan = disbursed(clients, store[2], proposal['id'])
    path = f'/api/v1/loans/{loan["id"]}/repayments'
    assert clients[1].post(path, json={'amount_minor': 100}, headers={'Idempotency-Key': 'repay-key-0001'}).status_code == 200
    assert clients[1].post(path, json={'amount_minor': 100}, headers={'Idempotency-Key': 'repay-key-0001'}).json()['paid_minor'] == 100
    assert clients[1].post(path, json={'amount_minor': 200}, headers={'Idempotency-Key': 'repay-key-0001'}).status_code == 409
    assert clients[0].post(path, json={'amount_minor': 100}, headers={'Idempotency-Key': 'repay-key-0002'}).status_code == 403


def test_notifications_queue_emails_once(store, clients, proposal):
    with store[0]() as db:
        # Proposal publication notified 3 members; the outbox provider queues one email per notification.
        assert db.scalar(select(func.count()).select_from(EmailDelivery)) == db.scalar(select(func.count()).select_from(Notification).where(Notification.category != 'chat'))


def test_gamification_ignores_simulated_money(store, clients, proposal):
    cid = store[2]
    for n in range(3):
        clients[0].post(f'/api/v1/circles/{cid}/contributions', json={'amount_minor': 500}, headers={'Idempotency-Key': f'gamify-fund-{n:03d}'})
    clients[0].post(f'/api/v1/loans/{proposal["id"]}/votes', json={'choice': 'support'})
    p = clients[0].get('/api/v1/me/progress').json()
    assert p['practice']['simulated_contributions'] == 3
    points = {b['event']: b['points'] for b in p['breakdown']}
    assert points['confirmed_contribution'] == 0 and points['vote_cast'] == 5 and points['circle_joined'] == 10
    assert p['level'] == 'Starter' and 'not a credit score' in p['notice']


def test_score_levels_and_caps():
    assert score([])['level'] == 'Starter'
    assert score([Event('vote_cast', 100)])['points'] == 100  # capped at 20 votes
    result = score([Event('confirmed_contribution', 20), Event('circle_joined', 5)])
    assert result['level'] == 'Circle Champion' and result['next_level'] is None


def test_health_reports_integrations_honestly(store):
    from fastapi.testclient import TestClient
    from app.main import app
    h = TestClient(app).get('/api/v1/health').json()
    assert h['ai'] == 'unavailable' and h['bmoni'] == 'unavailable' and h['bmoni_environment'] == 'sandbox' and h['financial_mode'] == 'simulation'


def test_loan_list_is_membership_scoped(store, clients, proposal):
    assert clients[0].get('/api/v1/loans').json()['total'] == 1
    assert clients[3].get('/api/v1/loans').json()['total'] == 0
