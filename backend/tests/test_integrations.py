import hashlib
import hmac
import json

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import bmoni, copilot, emails, integrations
from app.community import balances
from app.config import settings
from app.main import app
from app.models import AIInteraction, EmailDelivery, Journal, ProviderAccount, User, WebhookEvent

SECRET = 'a' * 64


@pytest.fixture
def cfg(monkeypatch):
    c = settings()
    monkeypatch.setattr(c, 'bmoni_api_key', 'pk_test_key_for_unit_tests')
    monkeypatch.setattr(c, 'bmoni_signer_seed', 's' * 40)
    monkeypatch.setattr(c, 'bmoni_webhook_secret', SECRET)
    return c


def mock_client(handler):
    return lambda: bmoni.BmoniClient(transport=httpx.MockTransport(handler))


# ---------- Brevo ----------

def test_brevo_success_records_provider_reference(store, monkeypatch):
    factory, ids, _ = store
    seen = {}

    def handler(request):
        seen['headers'], seen['body'] = request.headers, json.loads(request.content)
        return httpx.Response(201, json={'messageId': '<202610151200.1@smtp-relay.mailin.fr>'})
    monkeypatch.setattr(settings(), 'brevo_api_key', 'xkeysib-test')
    monkeypatch.setattr(settings(), 'brevo_sender_email', 'noreply@example.com')
    monkeypatch.setattr(emails, 'transport', httpx.MockTransport(handler))
    with factory.begin() as db:
        row = emails.send_now(db, db.get(User, ids[0]), 'verify', 'verify:test-1', code='123456', minutes=10, link='/verify')
        assert row.status == 'provider_accepted' and row.provider_message_id.startswith('<2026')
    assert seen['headers']['api-key'] == 'xkeysib-test'
    assert seen['body']['to'][0]['email'] == 'student0@students.example.edu'
    assert '123456' in seen['body']['textContent'] and seen['body']['htmlContent'].startswith('<!doctype html>')
    with factory() as db:
        assert '123456' not in json.dumps(db.scalar(select(EmailDelivery)).payload)


@pytest.mark.parametrize('response', [httpx.Response(400, json={'code': 'invalid_parameter'}), httpx.Response(201, json={}), httpx.Response(201, text='not json')])
def test_brevo_failures_never_claim_sent(store, monkeypatch, response):
    monkeypatch.setattr(settings(), 'brevo_api_key', 'xkeysib-test')
    monkeypatch.setattr(settings(), 'brevo_sender_email', 'noreply@example.com')
    monkeypatch.setattr(emails, 'transport', httpx.MockTransport(lambda _: response))
    r = TestClient(app).post('/api/v1/auth/register', json={'full_name': 'New Student', 'phone': '+2348031234567', 'university': 'Demo University',
                                                         'department': 'Physics', 'email': 'new@students.example.edu', 'password': 'a-long-password-123'})
    assert r.status_code == 503
    assert 'unavailable' in r.json()['detail']


def test_email_queue_dedupes_and_caps_retries(store, monkeypatch):
    factory, ids, _ = store
    calls = []
    monkeypatch.setattr(settings(), 'brevo_api_key', 'xkeysib-test')
    monkeypatch.setattr(settings(), 'brevo_sender_email', 'noreply@example.com')
    monkeypatch.setattr(emails, 'transport', httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(503)))
    with factory.begin() as db:
        user = db.get(User, ids[0])
        emails.enqueue(db, user, 'finance', 'notification:x', 'Title', 'Body', '/app')
        db.flush()
        emails.enqueue(db, user, 'finance', 'notification:x', 'Title', 'Body', '/app')
    for _ in range(4):
        with factory.begin() as db:
            emails.process_queue(db)
    with factory() as db:
        rows = db.scalars(select(EmailDelivery)).all()
        assert len(rows) == 1 and rows[0].status == 'failed' and rows[0].attempts == 3
    assert len(calls) == 3


# ---------- Groq copilot ----------

def groq(monkeypatch, handler):
    monkeypatch.setattr(settings(), 'groq_api_key', 'gsk_test')
    monkeypatch.setattr(copilot, 'provider_factory', lambda: copilot.GroqProvider('gsk_test', 'openai/gpt-oss-20b', 'https://api.groq.com/openai/v1', httpx.MockTransport(handler)))


def test_groq_answer_is_grounded_and_private(store, clients, proposal, monkeypatch):
    cid = store[2]
    clients[1].post(f'/api/v1/circles/{cid}/messages', json={'body': 'secret chat text'})
    seen = {}

    def handler(request):
        seen['url'], seen['body'] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': 'Total repayment is ₦105.32.'}}]})
    groq(monkeypatch, handler)
    r = clients[0].post('/api/v1/copilot', json={'question': 'Explain these terms', 'loan_id': proposal['id'], 'use_ai': True, 'consent': True})
    assert r.status_code == 200, r.text
    assert r.json()['source'] == 'groq' and r.json()['model'] == 'openai/gpt-oss-20b'
    assert seen['url'] == 'https://api.groq.com/openai/v1/chat/completions'
    assert seen['body']['include_reasoning'] is False and 'tools' not in seen['body']
    sent = json.dumps(seen['body'], ensure_ascii=False)
    assert '₦105.32' in sent  # server-calculated total supplied as a fact
    assert 'secret chat text' not in sent and 'Student 1' not in sent and 'student1@' not in sent


def test_groq_failure_and_consent(store, clients, monkeypatch):
    groq(monkeypatch, lambda _: httpx.Response(500))
    assert clients[0].post('/api/v1/copilot', json={'question': 'How much is saved?', 'use_ai': True}).status_code == 422
    r = clients[0].post('/api/v1/copilot', json={'question': 'How much is saved?', 'use_ai': True, 'consent': True})
    assert r.status_code == 503 and 'nothing was generated' in r.json()['detail']
    groq(monkeypatch, lambda _: httpx.Response(200, json={'choices': [{'message': {'content': ''}}]}))
    assert clients[0].post('/api/v1/copilot', json={'question': 'How much is saved?', 'use_ai': True, 'consent': True}).status_code == 503
    with store[0]() as db:
        assert db.scalar(select(func.count()).select_from(AIInteraction).where(AIInteraction.success.is_(False))) == 2


def test_copilot_permissions_and_deterministic_grounding(store, clients, proposal):
    cid = store[2]
    clients[0].post(f'/api/v1/circles/{cid}/contributions', json={'amount_minor': 123456}, headers={'Idempotency-Key': 'copilot-fund-01'})
    # A non-member cannot pull another circle's loan into the copilot context.
    assert clients[3].post('/api/v1/copilot', json={'question': 'Explain', 'loan_id': proposal['id']}).status_code == 404
    r = clients[0].post('/api/v1/copilot', json={'question': 'How much have we saved?'}).json()
    assert r['source'] == 'deterministic_guide' and r['intent'] == 'balance'
    assert '₦1,234.56' in r['answer']
    gaps = clients[0].post('/api/v1/copilot', json={'question': 'What is missing or unclear?', 'loan_id': proposal['id']}).json()
    assert 'No additional conditions' in gaps['answer']


# ---------- B Moni adapter ----------

def test_bmoni_create_user_and_failure_modes(cfg):
    def ok(request):
        assert request.headers['x-api-key'] == 'pk_test_key_for_unit_tests'
        assert str(request.url).startswith('https://embedded-dev.bmoni.com/v1/users')
        return httpx.Response(201, json={'user': {'id': 'x', 'bmoniUserId': 'bm-123', 'firstName': 'CampusCircle', 'email': 'e', 'createdAt': '', 'updatedAt': ''}})
    assert mock_client(ok)().create_user('A', 'B', 'a@example.com', '+2349000000001').extra['bmoni_user_id'] == 'bm-123'
    conflict = mock_client(lambda _: httpx.Response(409, json={'statusCode': 409, 'message': 'User already exists with this email', 'error': 'Conflict'}))()
    result = conflict.create_user('A', 'B', 'a@example.com', '+2349000000001')
    assert result.kind == 'failed' and result.status_code == 409 and 'already exists' in result.message

    def timeout(request):
        raise httpx.ReadTimeout('slow', request=request)
    assert mock_client(timeout)().create_user('A', 'B', 'a@example.com', '+2349000000001').kind == 'uncertain'
    assert mock_client(lambda _: httpx.Response(201, text='<html>'))().create_user('A', 'B', 'a@example.com', '+2349000000001').kind == 'uncertain'
    assert mock_client(lambda _: httpx.Response(201, json={'user': {}}))().create_user('A', 'B', 'a@example.com', '+2349000000001').kind == 'uncertain'
    assert mock_client(lambda _: httpx.Response(502))().balances('bm-1').kind == 'failed'  # reads never become "uncertain"
    item = {'smartWalletId': 'sw', 'currency': 'NGN', 'balance': '0', 'error': None}
    for shape in ({'data': {'balances': [item]}}, {'smartAccountAddress': '0x1', 'balances': [item]}):  # documented and live shapes
        assert mock_client(lambda _, shape=shape: httpx.Response(200, json=shape))().balances('bm-1').extra['balances'][0]['currency'] == 'NGN'
    assert mock_client(lambda _: httpx.Response(200, json={'balances': 'nope'}))().balances('bm-1').kind == 'failed'


def test_bmoni_wallet_signature_recovers_owner(cfg):
    account = bmoni.owner_account('circle-1')
    assert account.address == bmoni.owner_account('circle-1').address != bmoni.owner_account('circle-2').address

    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith('owner-proof-challenges'):
            assert body == {'currency': 'CNGN', 'userOwnerAddress': account.address}
            return httpx.Response(201, json={'challengeId': 'ch-1', 'groupId': 'g', 'message': 'Prove ownership 42', 'expiresAt': ''})
        recovered = Account.recover_message(encode_defunct(text='Prove ownership 42'), signature=body['ownerProofSignature'])
        assert recovered == account.address and body['ownerProofChallengeId'] == 'ch-1'
        return httpx.Response(201, json={'id': 'sw-1', 'currency': 'CNGN', 'walletAddress': '0xabc', 'isActive': True, 'createdAt': '', 'updatedAt': ''})
    result = mock_client(handler)().provision_wallet('bm-1', account)
    assert result.ok and result.extra['wallet_id'] == 'sw-1'


def test_treasury_endpoint_permissions_and_flow(store, clients, cfg, monkeypatch):
    cid = store[2]

    def handler(request):
        if request.url.path == '/v1/users':
            return httpx.Response(201, json={'user': {'bmoniUserId': 'bm-circle'}})
        if request.url.path.endswith('owner-proof-challenges'):
            return httpx.Response(201, json={'challengeId': 'ch', 'groupId': 'g', 'message': 'm', 'expiresAt': ''})
        if request.url.path.endswith('create-managed'):
            return httpx.Response(201, json={'id': 'sw-1', 'currency': 'CNGN', 'walletAddress': '0xwallet', 'isActive': True})
        return httpx.Response(200, json={'data': {'balances': [{'smartWalletId': 'sw-1', 'currency': 'NGN', 'balance': '1000.000000'}]}})
    monkeypatch.setattr(integrations, 'client_factory', mock_client(handler))
    assert clients[1].post(f'/api/v1/circles/{cid}/treasury').status_code == 403
    assert clients[3].get(f'/api/v1/circles/{cid}/treasury').status_code == 404
    r = clients[0].post(f'/api/v1/circles/{cid}/treasury')
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'active' and r.json()['external_user_id'] == 'bm-circle'
    assert 'external_user_id' not in clients[1].get(f'/api/v1/circles/{cid}/treasury').json()
    bal = clients[0].get(f'/api/v1/circles/{cid}/treasury/balance').json()
    assert bal['source'] == 'provider_reported' and bal['balances'][0]['balance'] == '1000.000000'


def test_treasury_unavailable_without_credentials(store, clients):
    r = clients[0].post(f'/api/v1/circles/{store[2]}/treasury')
    assert r.status_code == 503 and 'not configured' in r.json()['detail']


# ---------- Webhooks ----------

def signed(body: dict):
    raw = json.dumps(body).encode()
    return raw, {'X-Webhook-Signature': hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(), 'Content-Type': 'application/json'}


def test_webhook_signature_dedupe_and_separation(store, clients, cfg):
    factory, _, cid = store
    with factory.begin() as db:
        db.add(ProviderAccount(provider='bmoni', circle_id=cid, external_user_id='bm-circle', owner_address='0x1', status='active'))
    api = TestClient(app)
    event = {'id': 'evt-1', 'eventType': 'employee.deposit.completed', 'payload': {'userId': 'bm-circle', 'amount': '1000.00'}, 'timestamp': ''}
    raw, headers = signed(event)
    assert api.post('/api/v1/webhooks/bmoni', content=raw, headers={**headers, 'X-Webhook-Signature': '0' * 64}).status_code == 401
    assert api.post('/api/v1/webhooks/bmoni', content=raw + b' ', headers=headers).status_code == 401  # signature covers raw bytes
    first = api.post('/api/v1/webhooks/bmoni', content=raw, headers=headers)
    assert first.json() == {'received': True, 'status': 'processed'}
    assert api.post('/api/v1/webhooks/bmoni', content=raw, headers=headers).json()['duplicate'] is True
    with factory() as db:
        journal = db.scalar(select(Journal))
        assert journal.provenance == 'bmoni_sandbox' and journal.status == 'provider_confirmed' and journal.amount_minor == 100000
        b = balances(db, cid)
        assert b['sandbox_cash_minor'] == 100000 and b['cash_minor'] == 0 and b['available_minor'] == 0
    for eid, payload in (('evt-2', {'userId': 'someone-else', 'amount': '5.00'}), ('evt-3', {'userId': 'bm-circle', 'amount': '10.001'})):
        raw, headers = signed({'id': eid, 'eventType': 'employee.deposit.completed', 'payload': payload})
        assert api.post('/api/v1/webhooks/bmoni', content=raw, headers=headers).status_code == 200
    with factory() as db:
        assert {e.event_id: e.status for e in db.scalars(select(WebhookEvent))} == {'evt-1': 'processed', 'evt-2': 'unmatched', 'evt-3': 'rejected'}
        assert db.scalar(select(func.count()).select_from(Journal)) == 1


def test_parse_amount_is_exact():
    assert bmoni.parse_amount_minor('0.29') == 29 and bmoni.parse_amount_minor('1000') == 100000
    for bad in ('10.001', '-1.00', 'abc', 12.5, '0'):
        with pytest.raises(ValueError):
            bmoni.parse_amount_minor(bad)
